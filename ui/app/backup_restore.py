"""Restore flows: rollback (hot), full recovery (cold), logs merge (spec §6)."""
from __future__ import annotations
import csv as _csv
import gzip as _gzip
import io as _io
import json
from pathlib import Path
from typing import Any

import yaml

from app.config_render import render_config
from app.config_engine import _make_resolve_key
from app.config_store import write_config_atomic
from app.model_reconcile import reconcile_models
from app.mcp_reconcile import build_desired as build_mcp_desired, mcp_content_diff, reconcile_mcp
from app.backup_engine import verify_manifest, pg_restore_cmd, fingerprints as _fps
from app.backup_tables import NEVER_RESTORE, classify, base_tables

RESTART_KINDS = {"router_setting", "litellm_setting", "general_setting", "passthrough"}


def parse_export(text: str) -> list[dict]:
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}")
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ValueError("expected {version, items: [...]} export shape")
    for it in items:
        if not isinstance(it, dict) or "kind" not in it or "name" not in it or "data" not in it:
            raise ValueError("every item needs kind, name, data")
    return items


def rollback_preview(current: list[dict], new: list[dict]) -> dict:
    cur = {(i["kind"], i["name"]): i["data"] for i in current}
    nxt = {(i["kind"], i["name"]): i["data"] for i in new}
    added = sorted(set(nxt) - set(cur))
    removed = sorted(set(cur) - set(nxt))
    changed = sorted(k for k in set(cur) & set(nxt) if cur[k] != nxt[k])
    touched = added + removed + changed
    return {"added": [{"kind": k, "name": n} for k, n in added],
            "removed": [{"kind": k, "name": n} for k, n in removed],
            "changed": [{"kind": k, "name": n} for k, n in changed],
            "restart_kinds_changed": any(k in RESTART_KINDS for k, _ in touched)}


def check_decryptable(items: list[dict], fernet) -> list[str]:
    bad = []
    for it in items:
        d = it.get("data")
        if not isinstance(d, dict):
            continue
        for field in ("value_encrypted", "auth_value_encrypted"):
            v = d.get(field)
            if v:
                try:
                    fernet.decrypt(v.encode())
                except Exception:
                    bad.append(f'{it["kind"]}/{it["name"]}')
                break
    return bad


async def rollback_config(items: list[dict], *, config_store, models_client, mcp_client,
                          reloader, config_path: str, fernet, hybrid: bool) -> dict:
    """Replace the master with `items`, then converge exactly like resync + a
    settings-diff-driven restart (spec §6.1). Caller has already run pre-checks.

    hybrid=True (STORE_MODEL_IN_DB=true): models/MCP servers are reconciled
    against LiteLLM's live DB-model API; config.yaml stays settings-only and is
    only rewritten (and the proxy only restarted) when it actually changed.
    hybrid=False: LiteLLM has no DB-model API to reconcile against — models and
    credentials live only in config.yaml, so we render the FULL config (with
    model_list/credential_list inlined) and always rewrite + restart to pick it
    up, matching how non-hybrid applies already behave.
    """
    dec = lambda b: fernet.decrypt(b.encode()).decode()
    await config_store.replace_applied(items)
    applied = await config_store.applied()

    model_items = [it for it in applied if it["kind"] == "model"]
    expected = [(it["data"] or {}).get("model_name", it["name"]) for it in model_items]

    if hybrid:
        resolve_key = _make_resolve_key(applied, dec)
        live = await models_client.list_models()
        model_report = await reconcile_models(model_items, live, models_client,
                                              changed_item_names={it["name"] for it in model_items},
                                              creds_changed=set(), resolve_key=resolve_key,
                                              converge_content=True)
        mcp_items = [it for it in applied if it["kind"] == "mcp_server"]
        try:
            mcp_live = await mcp_client.list_servers()
            desired_mcp, _ = build_mcp_desired(mcp_items, None)
            live_by_id = {s.get("server_id"): s for s in mcp_live if s.get("server_id")}
            drifted = {sid for sid in (set(desired_mcp) & set(live_by_id))
                       if mcp_content_diff(desired_mcp[sid], live_by_id[sid])}
            mcp_report = await reconcile_mcp(mcp_items, mcp_live, mcp_client, drifted, dec)
        except Exception as e:
            mcp_report = {"added": 0, "updated": 0, "deleted": 0,
                          "failed": [{"id": "*", "op": "list", "error": str(e)}]}
    else:
        # No DB-model API to reconcile against on a non-hybrid install: models/
        # credentials only exist in config.yaml, so there's nothing to converge.
        model_report = {"added": 0, "updated": 0, "deleted": 0, "failed": []}
        mcp_report = {"added": 0, "updated": 0, "deleted": 0, "failed": []}

    out: dict[str, Any] = {"applied": True, "models": model_report, "mcp": mcp_report}

    if hybrid:
        rendered = render_config(applied, dec, hybrid=True)
        try:
            on_disk = yaml.safe_load(open(config_path)) or {}
        except OSError:
            on_disk = None
        if rendered != on_disk:
            write_config_atomic(config_path, yaml.safe_dump(rendered, sort_keys=False))
            try:
                await reloader.reload_and_verify(expected)
                out["restart"] = "healthy"
            except Exception as e:
                out["restart"] = "unhealthy"; out["detail"] = str(e)
        else:
            out["restart"] = "skipped"
    else:
        rendered = render_config(applied, dec)
        write_config_atomic(config_path, yaml.safe_dump(rendered, sort_keys=False))
        try:
            await reloader.reload_and_verify(expected)
            out["restart"] = "healthy"
        except Exception as e:
            out["restart"] = "unhealthy"; out["detail"] = str(e)
    return out


def truncate_statement(tables: list[str]) -> str:
    keep = [t for t in tables if t not in NEVER_RESTORE]
    return "TRUNCATE " + ", ".join(f'"{t}"' for t in keep)


def check_fingerprints(manifest: dict, salt_key, fernet_secret) -> list[str]:
    want = manifest.get("fingerprints") or {}
    have = _fps(salt_key, fernet_secret)
    return [k for k in ("salt", "fernet")
            if want.get(k) and have.get(k) and want[k] != have[k]]


async def full_recovery(bdir: Path, *, dsn, reloader, config_path, connect,
                        run_subprocess, salt_key, fernet_secret) -> dict:
    steps: list[dict] = []
    def step(name, status, detail=""):
        steps.append({"step": name, "status": status, "detail": detail})
        return status == "ok"

    manifest, errs = verify_manifest(bdir)
    if manifest is None or errs:
        step("verify_backup", "error", "; ".join(errs or ["no manifest"]))
        return {"ok": False, "steps": steps}
    step("verify_backup", "ok")

    mism = check_fingerprints(manifest, salt_key, fernet_secret)
    if mism:
        step("fingerprints", "error",
             f"backup was made under different secrets ({', '.join(mism)}) — refusing")
        return {"ok": False, "steps": steps}
    step("fingerprints", "ok")

    # Pre-check: connect + read the live schema + compute restore targets BEFORE
    # touching the container. Nothing destructive has happened yet, so on any
    # failure here we report it as a truncate-step error and return WITHOUT
    # ever calling reloader.stop().
    try:
        conn = await connect()
    except Exception as e:
        step("truncate", "error", f"could not read live schema: {e}")
        return {"ok": False, "steps": steps}

    try:
        try:
            live_config = set(classify(await base_tables(conn))["config"])
        except Exception as e:
            step("truncate", "error", f"could not read live schema: {e}")
            return {"ok": False, "steps": steps}

        targets = [t for t in manifest.get("tables", []) if t in live_config]
        missing = [t for t in manifest.get("tables", []) if t not in live_config]
        if not targets:
            step("truncate", "error", "no restorable tables in this backup match the live schema")
            return {"ok": False, "steps": steps}

        try:
            await reloader.stop(); step("stop", "ok")
        except Exception as e:
            step("stop", "error", str(e))
            try:                                    # best-effort: don't leave LiteLLM down
                await reloader.start(); step("start", "ok")
            except Exception as e2:
                step("start", "error", str(e2))
            return {"ok": False, "steps": steps}

        ok = True
        stage = "truncate"
        try:
            await conn.execute(truncate_statement(targets))
            step("truncate", "ok", f"skipped (not live): {missing}" if missing else "")

            stage = "pg_restore"
            argv, env = pg_restore_cmd(dsn, str(bdir / "litellm-config.dump"))
            rc, err = await run_subprocess(argv, env)
            if rc != 0:
                ok = step("pg_restore", "error", err) and ok
            else:
                step("pg_restore", "ok")

            if ok:
                stage = "config_yaml"
                write_config_atomic(config_path, (bdir / "config.yaml").read_text())
                step("config_yaml", "ok")
        except Exception as e:
            ok = step(stage, "error", str(e)) and ok
        finally:
            try:
                await reloader.start(); step("start", "ok")
                await reloader.verify([]); step("ready", "ok")
            except Exception as e:
                ok = step("start", "error", str(e)) and ok
        return {"ok": ok and all(s["status"] == "ok" for s in steps), "steps": steps}
    finally:
        await conn.close()


def merge_sql(table: str, csv_cols: list[str], live_cols: list[str]) -> dict:
    live = set(live_cols)
    used = [c for c in csv_cols if c in live]
    dropped = [c for c in csv_cols if c not in live]
    cols = ", ".join(f'"{c}"' for c in used)
    return {"temp": f'CREATE TEMP TABLE _restore (LIKE "{table}" INCLUDING DEFAULTS) ON COMMIT DROP',
            "copy_columns": used, "dropped": dropped,
            "insert": (f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM _restore '
                       f'ON CONFLICT DO NOTHING')}


def _tag_count(tag: str) -> int:
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError):
        return 0


async def restore_logs(slice_dirs: list, connect) -> dict:
    tables: dict[str, dict] = {}
    ok = True
    conn = await connect()
    try:
        for d in slice_dirs:
            for gz in sorted(Path(d).glob("*.csv.gz")):
                table = gz.name.removesuffix(".csv.gz")
                try:
                    with _gzip.open(gz, "rt", newline="") as f:
                        header = next(_csv.reader(f))
                    live_cols = [r["column_name"] for r in await conn.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position", table)]
                    if not live_cols:
                        tables.setdefault(table, {"inserted": 0, "skipped": 0,
                                                  "dropped_columns": [], "error": "table not live"})
                        continue
                    m = merge_sql(table, header, live_cols)
                    async with conn.transaction():
                        await conn.execute(m["temp"])
                        src = _gzip.open(gz, "rb")
                        if m["dropped"]:
                            keep_idx = [header.index(c) for c in m["copy_columns"]]
                            buf = _io.StringIO()
                            w = _csv.writer(buf)
                            with _gzip.open(gz, "rt", newline="") as f2:
                                r = _csv.reader(f2)
                                for row in r:
                                    w.writerow([row[i] for i in keep_idx])
                            src = _io.BytesIO(buf.getvalue().encode())
                        copy_tag = await conn.copy_to_table(
                            "_restore", source=src,
                            columns=m["copy_columns"], format="csv", header=True)
                        copied = _tag_count(str(copy_tag))
                        inserted = _tag_count(await conn.execute(m["insert"]))
                    agg = tables.setdefault(table, {"inserted": 0, "skipped": 0,
                                                    "dropped_columns": m["dropped"]})
                    agg["inserted"] += inserted
                    agg["skipped"] += max(copied - inserted, 0)
                except Exception as e:
                    ok = False
                    tables.setdefault(table, {"inserted": 0, "skipped": 0,
                                              "dropped_columns": []})["error"] = str(e)
    finally:
        await conn.close()
    return {"ok": ok, "tables": tables}
