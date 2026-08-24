# Backup, Restore & Request Logging (v3.30) — Design

**Motivation.** On 2026-08-24 a `docker compose pull` brought a LiteLLM build whose default
migration resolver dropped every `ui_*` table (the master config); a subsequent Resync then
deleted all live models. Recovery required forensic WAL/dead-tuple extraction because no backup
existed. This design adds scheduled backups, one-click restore, and the guard that would have
stopped that Resync — and folds in request/response logging, whose periodic export rides the
same backup pipeline and doubles as a fine-tuning dataset.

**Goal.** The UI backs itself up on a schedule to a local mount, can restore from those backups
in one click (config rollback, full recovery, or logs merge), refuses the destructive
empty-master Resync, and can capture full-fidelity request/response bodies reviewable in the
Activity feed and exported as CSV slices.

---

## 1. Scope

**In scope**
- Two independent backup tiers — **config** and **logs** — each with its own schedule,
  retention, run history, and "Back up now".
- Auto **snapshot** of the master config after every successful Apply.
- Three restore flows: **rollback config** (hot), **full recovery** (cold, data-only),
  **restore logs** (merge, never destructive).
- Empty-master guard on Resync/Apply + detection banner + backup-staleness banner.
- Settings → **Backup & Restore** tab; Settings → **Request logging** card.
- Request/response capture via `store_prompts_in_spend_logs` with truncation disabled;
  Activity-feed transaction detail shows request and response; backup files downloadable
  from the UI.
- Compose/Dockerfile changes: `postgresql-client-16`, `./backups` mount, `TZ` passthrough,
  `MAX_STRING_LENGTH_PROMPT_IN_DB`.

**Out of scope**
- Off-box replication of the backup mount (user-owned).
- Restoring `.env` secrets. A backup is only usable with the same `LITELLM_SALT_KEY` and
  `SESSION_SECRET`/`CREDENTIALS_KEY`; the manifest records non-reversible fingerprints so a
  mismatch is *reported*, never guessed.
- Per-key logging opt-outs and body-scrub jobs (user chose full retention; LiteLLM's key-level
  `turn_off_message_logging` remains available and is documented, not built).
- Restore of usage aggregates to perfect mid-day accuracy (documented limitation, §6.3).

---

## 2. Table classification (single source of truth: `ui/app/backup_tables.py`)

- **USAGE_TABLES** (excluded from config dumps; exported by the logs tier):
  `LiteLLM_SpendLogs`, `LiteLLM_SpendLogToolIndex`, `LiteLLM_SpendLogGuardrailIndex`,
  every `LiteLLM_Daily*` table (matched by prefix at runtime), `LiteLLM_ErrorLogs`,
  `LiteLLM_AuditLog`.
- **TRANSIENT_TABLES** (in neither tier): `LiteLLM_HealthCheckTable`.
- **Config tier** = every other base table in `public`, including all `ui_*` tables and
  `_prisma_migrations` (schema dumped for provenance; its DATA is excluded — restoring it would
  conflict with the live migration state; never truncated or restored — §6.2).
- The module exposes `usage_tables(conn)` / `config_tables(conn)` that resolve the live
  table list via `information_schema` (prefix matching keeps new `LiteLLM_Daily*` tables
  classified correctly after LiteLLM upgrades). Views are ignored everywhere.
- Logs-tier export strategy per table (verified against the live schema during
  implementation and pinned in code):
  - watermark tables — `LiteLLM_SpendLogs` (`"startTime"`), `LiteLLM_SpendLogToolIndex`
    and `LiteLLM_SpendLogGuardrailIndex` (their start-time column), `LiteLLM_ErrorLogs`
    (`"startTime"`), `LiteLLM_AuditLog` (`updated_at`): export rows in
    `(last_exported, now() − 60 s]` — the 60 s guard avoids racing LiteLLM's 10 s batch
    writer.
  - rolling-window tables — all `LiteLLM_Daily*` aggregates: export rows with
    `date >= today − 3 days` each run (rows mutate all day; re-exporting recent days keeps
    slices usefully fresh).
  - A table whose expected column is missing is skipped with a warning recorded in the
    manifest — never a crash.

## 3. On-disk layout (`BACKUP_DIR`, default `/backups`)

```
/backups/
  settings.json                          # mirror of ui_settings backup config (self-heal, §5)
  config/<STAMP>/
    litellm-config.dump                  # pg_dump -Fc --no-owner --no-privileges,
                                         #   --exclude-table for every usage/transient table
    ui_config.json                       # master export (same payload as /api/config/export)
    config.yaml                          # the rendered file as it was
    manifest.json
  snapshots/<STAMP>-apply.json           # ui_config.json auto-written after each successful Apply
  logs/<STAMP>/
    LiteLLM_SpendLogs.csv.gz             # COPY … TO STDOUT (FORMAT csv, HEADER), gzipped
    <other usage tables>.csv.gz
    manifest.json
```

- `<STAMP>` = local time with offset, filesystem-safe: `2026-08-24T03-00-00+1000`.
- Directories `0700`, files `0600`.
- `manifest.json` fields: `tier`, `taken_at` (ISO, local), `ui_version`, `litellm_image_digest`
  (best-effort via socket-proxy inspect), `pg_version`, per-table row counts, per-file sha256
  and bytes, secret fingerprints (`sha256(salt_key)[:12]`, `sha256(fernet_secret)[:12]`),
  config tier: item counts by kind + the excluded-table list; logs tier: per-table slice
  window `[from, to)` and watermarks.
- Retention prune runs after each successful run of its tier: delete backup directories of
  that tier older than `retention_days` (by manifest `taken_at`; `0` = keep forever). Only
  directories containing a valid manifest are ever deleted. Snapshots: keep newest 50.
- Logs watermark for the next run = read from the newest logs manifest on disk (not from the
  DB), so it survives full recovery and `ui_*` loss.

## 4. Engine (`ui/app/backup_engine.py`)

- `pg_dump`/`pg_restore` binaries come from `postgresql-client-16` (PGDG apt repo) added to
  the UI image. Connection parameters are derived from `settings.database_url`; the password
  travels via the `PGPASSWORD` env of the subprocess, never argv.
- Config run: dump → write `ui_config.json` + `config.yaml` copy → manifest → prune.
  Any failure deletes the partial directory and records the error.
- Logs run: per-table `asyncpg` `copy_from_query(..., format='csv', header=True)` streamed
  through gzip → manifest → prune. A run with zero new rows in every table still writes a
  manifest (slice `[from, from)`) so staleness is measurable, but skips empty CSV files.
- One run at a time per tier (`asyncio.Lock`); a second trigger returns `409 already running`.
- Every run writes a `ui_backup_runs` row: `id, tier, started_at, finished_at,
  status(running|ok|error), path, bytes, error, meta jsonb`.

## 5. Settings, scheduler, self-heal

- New `ui_settings` table: `key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz
  DEFAULT now()` (created `IF NOT EXISTS` like the other ui tables). Keys `backup.config` and
  `backup.logs`, value shape:
  `{"enabled": bool, "frequency": {"kind": "daily"|"weekly"|"every_n_days", "weekday": 0-6?,
  "n": int?}, "time": "HH:MM", "retention_days": int}`.
- Defaults when unset: config `{enabled: true, daily, 03:00, retention 14}`;
  logs `{enabled: false, daily, 03:30, retention 0}`.
- Every settings save also rewrites `/backups/settings.json`. On boot, if `ui_settings` has no
  backup keys but the mirror exists, the mirror is imported (the schedule survives a ui_* wipe).
- Scheduling on the existing lifespan `AsyncIOScheduler`: `CronTrigger(hour, minute[,
  day_of_week])` for daily/weekly; `IntervalTrigger(days=n, start_date=<next HH:MM>)` for
  every-N-days. Triggers run in the container's local TZ — compose passes `TZ` through
  (`TZ: ${TZ:-UTC}`; `.env.example` gains `TZ=`). Saving settings re-registers jobs live
  (`scheduler.add_job(..., replace_existing=True)` / `remove_job`); no restart.
- Pure function `build_trigger(settings) -> trigger` keeps this unit-testable.

## 6. Restore flows (all POST routes require a typed confirmation string in the body)

### 6.1 Rollback config (hot) — from any snapshot or a config backup's `ui_config.json`
1. Preview endpoint returns the diff vs the current master: items added / removed / changed
   (by `kind`+`name`, with `data` equality), plus which changed items are restart-kind.
2. Pre-checks (refuse before writing anything): every `value_encrypted` /
   `auth_value_encrypted` in the file must decrypt with the current Fernet secret; the file
   must parse as a `{version: 1, items: [...]}` export.
3. Execute in one transaction: `DELETE FROM ui_config_applied; INSERT` all items;
   `DELETE FROM ui_config_staged` (staged work is explicitly discarded — stated in the
   confirm dialog).
4. Converge exactly as Apply does post-commit: reconcile models + MCP against LiteLLM;
   if the re-rendered settings config differs from the on-disk `config.yaml`, atomic-write it
   and restart LiteLLM via the reloader. Report the same shaped result as Apply.
   Confirmation string: `ROLLBACK`.

### 6.2 Full recovery (cold) — from a config backup
1. Pre-checks: manifest present and file hashes verify; secret fingerprints match the current
   env (mismatch → refuse with a clear message); dump's table list resolved against the live
   schema (tables missing live are reported and skipped; live config tables absent from the
   manifest are reported and left untouched).
2. Stop LiteLLM (socket-proxy `POST /containers/<litellm>/stop`).
3. `TRUNCATE <all manifest config tables that exist live> CASCADE`-free: one statement listing
   every table, which satisfies the FK graph (all FKs are inside the config tier — verified
   2026-08-24). `_prisma_migrations` is excluded: the live schema and migration state are
   preserved (neither its data is restored nor its rows are truncated), which is what makes old
   backups restorable after LiteLLM upgrades.
4. `pg_restore --data-only --disable-triggers --no-owner` of `litellm-config.dump`
   (superuser connection; sequences restored via the dump's `setval`s).
5. Copy the backup's `config.yaml` over the live one (atomic write).
6. Start LiteLLM, wait for readiness (reuse reloader verify), run the drift check, and return
   a step log (`stop → truncate → restore → config.yaml → start → ready → drift`) with
   per-step status. On a mid-flight failure LiteLLM is still started, the step log shows the
   failure, and the operation is retryable (the backup directory is read-only input).
   Usage tables are never touched. Confirmation string: `RECOVER`.

### 6.3 Restore logs (merge) — one slice or all slices of the logs tier
For each CSV: create a `TEMP` table shaped by the CSV header ∩ live columns → `COPY` in →
`INSERT INTO <table> (<cols>) SELECT … ON CONFLICT DO NOTHING` (conflict target = the table's
primary key, read from the catalog). Header columns unknown to the live table are dropped with
a warning; live columns missing from the file take defaults. Returns per-table
inserted/skipped counts. Never deletes or updates existing rows — restored `Daily*` aggregate
rows may therefore be stale snapshots where the live row was lost (accepted; `SpendLogs` is
the authoritative record). Confirmation string: `MERGE`.

## 7. Guard & detection

- **Empty-master guard**: `POST /api/config/resync` and `POST /api/apply` return
  `409 {"detail": "master config is empty but LiteLLM serves N models — refusing to delete
  them; restore from Backup & Restore, or pass force:true to wipe deliberately"}` when the
  effective master has zero non-deleted `model` items while LiteLLM's `/v1/model/info` lists
  ≥ 1. Body `{"force": true}` overrides (the UI sends it only from a confirm dialog that
  shows both counts).
- **`GET /api/backup/status`** returns per tier: last ok run (time, path, bytes), last error,
  next scheduled fire time, running flag, `stale` (no ok run within 2× the configured
  interval while enabled), plus `master_empty_live_nonempty` and the live/master model counts.
- `App.svelte` fetches status once after login; a dismissable top banner appears for
  `master_empty_live_nonempty` (danger, links to Backup & Restore) and for stale/failed
  backups (warning).
- Boot self-heal (§5) plus a WARNING log when the bootstrap seeder finds `ui_config_applied`
  empty while LiteLLM reports models.

## 8. Request/response logging

- **Enable/disable** via a Settings card ("Request & response logging"): stages the
  `general_setting` item `store_prompts_in_spend_logs` = `true|false` and prompts the normal
  Apply (restart-kind → config.yaml render + ~25 s LiteLLM restart). The card states what
  gets stored (full request body incl. messages/tools, full response, per SpendLogs row),
  where (`LiteLLM_SpendLogs.proxy_server_request` / `.response`), and the privacy note
  (email/memory content for rspamd/hindsight-class keys; per-key opt-out documented:
  key `metadata.turn_off_message_logging: true`).
- **No truncation**: compose sets `MAX_STRING_LENGTH_PROMPT_IN_DB: "10000000"` on the litellm
  service (LiteLLM's default truncates every string to 2 048 chars). Static compose config —
  not a UI setting.
- **Review in GUI**: `GET /api/usage/tx/{id}` additionally selects
  `proxy_server_request, response, messages`; `_shape_tx` adds
  `request` (parsed body) and `response`. The ActivityFeed detail pane renders, when present:
  a transcript view (one block per message: role badge + content; tool calls rendered as
  code), the response content the same way, a "raw JSON" toggle, and copy buttons. Absent
  bodies (logging off, or pre-enable rows) show "not captured".
- **Export**: the logs backup tier already exports `LiteLLM_SpendLogs` as CSV slices that now
  include the body columns — this *is* the periodic CSV export and the fine-tuning dataset
  feed. `GET /api/backup/download?path=<id>/<file>` streams any backup file (path validated,
  §9). The Backup page shows a download icon per file; enabling the logging card hints
  "enable the logs backup tier so bodies are exported before housekeeping prunes them (90 d)".
- Growth expectation at current traffic (~8.5 k req/30 d, avg 5.1 k prompt tokens): roughly
  6 MB/day of body text in the DB; housekeeping's 90-day SpendLogs retention bounds the DB
  while daily slices preserve everything on the mount (logs retention default 0 = forever).

## 9. API surface (all `login_required`, under `/api/backup`)

| Route | Method | Body / params | Purpose |
|---|---|---|---|
| `/api/backup/status` | GET | — | §7 status blob |
| `/api/backup/settings` | GET/PUT | two tier objects | read/save schedules; PUT re-registers jobs |
| `/api/backup/run` | POST | `{tier}` | run now; 409 if running |
| `/api/backup/list` | GET | — | backups (both tiers, manifest summaries) + snapshots |
| `/api/backup/rollback/preview` | GET | `?source=<id>` | diff vs current master |
| `/api/backup/rollback` | POST | `{source, confirm:"ROLLBACK"}` | §6.1 |
| `/api/backup/recover` | POST | `{source, confirm:"RECOVER"}` | §6.2, returns step log |
| `/api/backup/restore-logs` | POST | `{source:"<id>"\|"all", confirm:"MERGE"}` | §6.3 |
| `/api/backup/download` | GET | `?path=<id>/<file>` | stream a backup file |
| `/api/backup/item` | DELETE | `{path}` | delete one backup dir / snapshot |

`source`/`path` values are ids like `config/2026-08-24T03-00-00+1000` or
`snapshots/<stamp>-apply.json`: validated against `^(config|logs|snapshots)/[A-Za-z0-9+._-]+
(/[A-Za-z0-9._-]+)?$` and resolved with `Path.resolve()` confined under `BACKUP_DIR`.

## 10. UI

- `Settings.svelte` becomes two tabs: **General** (existing cards + the new Request-logging
  card) and **Backup & Restore** (new `BackupRestore.svelte`):
  status strip (per tier: last ok, next run, size, running; Back up now) → schedule cards
  (enabled/frequency/time/retention, Save) → backups table (tier, taken at, size, manifest
  counts; actions Rollback / Full recovery / Restore logs / Download / Delete) → snapshots
  list (rollback per row) → run history (last 20/tier with errors).
- Apply-snapshot hook: `config_v3_routes.apply` writes `snapshots/<stamp>-apply.json` after a
  successful `apply_config` (best-effort; failure logged, never fails the Apply).
- All destructive dialogs require typing the confirmation word and show what will happen
  (rollback: the diff + "staged changes will be discarded"; recovery: "stops the proxy ~1 min,
  replaces config/keys/teams/models; usage logs untouched").

## 11. Security & error handling

- Backup tree `0700`/`0600`; dumps contain LiteLLM-salt-encrypted and Fernet-encrypted secrets
  only (same posture as the DB itself); manifests contain fingerprints, never secrets.
- `PGPASSWORD` via subprocess env; subprocess output captured and stored in the run row on
  failure (stderr tail, 4 KB cap).
- Scheduler jobs never raise; every failure lands in `ui_backup_runs` + `/api/backup/status`.
- Restores are refused (never partial) on pre-check failure; full recovery's only
  non-retryable window is between TRUNCATE and restore completion, and its failure handling
  restarts LiteLLM and reports — the input backup is immutable so retry is always possible.

## 12. Testing

- **Unit (pytest, existing fake style)**: table classification incl. `Daily*` prefix; pg_dump
  argument builder (exclusions, PGPASSWORD, URI parsing); manifest build/verify; retention
  selection (never deletes non-manifest dirs; 0 = never); `build_trigger` for all three
  frequencies incl. TZ; watermark math incl. the 60 s guard and empty-slice runs; TRUNCATE
  statement builder; CSV merge SQL builder (header ∩ columns, PK conflict target, unknown-column
  warning); rollback preview diff; decrypt pre-check; secret-fingerprint check; guard predicate
  + both 409 routes + force override; every `/api/backup/*` route against a fake engine
  (incl. path-traversal rejection); `_shape_tx` body fields; apply-snapshot hook failure
  isolation.
- **Live proof (dev stack, `build ./ui`, LAN IP 10.0.20.85)**: scheduled config run fires and
  files/manifest/permissions verify → Apply produces a snapshot → rollback to it (drift
  `in_sync`) → simulate the incident (drop `ui_config_*`; Resync refused; banner; full
  recovery; model/key counts match manifest) → enable request logging via the card, make a
  chat call, see request/response in Activity detail → logs run exports bodies; delete rows;
  restore-logs merges them back (counts match, no duplicates) → download a slice → Playwright
  walkthrough.

## 13. Rollout

- Dockerfile: PGDG repo + `postgresql-client-16` (~40 MB).
- Compose: UI service gains `./backups:/backups`, `BACKUP_DIR=/backups`, `TZ: ${TZ:-UTC}`;
  litellm service gains `MAX_STRING_LENGTH_PROMPT_IN_DB: "10000000"`; `.env.example` gains
  `TZ=`. README + `docs/admin-ui-guide.md` get a Backup & Restore section (including the
  "same secrets required" caveat and the pre-upgrade manual backup advice).
- Release: UI **1.35.0**. Deploy on .75: set `TZ=Australia/Sydney` in `.env`, `mkdir backups`,
  `docker compose pull llm-proxy-ui && docker compose up -d --no-deps llm-proxy-ui`
  (LiteLLM untouched; its compose-only env/command changes take effect at its next recreate).
  First action: **Back up now** (config tier). Enabling request logging is a separate,
  user-initiated Apply (~25 s proxy restart) at a moment of the user's choosing.
