from __future__ import annotations

# A model group is referenced from several places; a reference is an ORPHAN iff
# its literal name is not a current group (provider-stripping is Phase 2, not here).

_FALLBACK_RULE_SETTINGS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")


def _missing(ref, valid: set) -> bool:
    """True iff `ref` is a real (hashable, non-empty) name absent from `valid`.
    A non-str/unhashable leaf (a dict/list where a name belongs) is malformed → not missing (skipped)."""
    return isinstance(ref, str) and bool(ref) and ref not in valid


def group_names(model_items: list[dict]) -> set[str]:
    """Distinct public model_name across non-deleted model items (effective)."""
    return {(it.get("data") or {}).get("model_name")
            for it in model_items
            if it.get("kind") == "model" and it.get("flag") != "deleted"
            and (it.get("data") or {}).get("model_name")}


def _orphan(scope, location, reference, target):
    return {"scope": scope, "location": location, "reference": reference,
            "missing": [reference], "target": target}


def router_orphans(router_items: list[dict], groups: set[str]) -> list[dict]:
    """Scan fallback variants (list[{primary:[targets]}]), default_fallbacks (list[str]),
    and model_group_alias ({alias:target}). One orphan record per dangling name."""
    out: list[dict] = []
    for it in router_items:
        name, data = it.get("name"), it.get("data")
        if name in _FALLBACK_RULE_SETTINGS:
            if not isinstance(data, list):
                continue
            for rule in data:
                if not isinstance(rule, dict):
                    continue
                for primary, targets in rule.items():
                    if _missing(primary, groups):
                        out.append(_orphan("router", f"router_settings.{name}", primary,
                                           {"setting": name, "primary": primary, "dangling": primary}))
                        continue                      # rule is doomed; don't also flag its targets
                    if not isinstance(targets, list):
                        continue
                    for t in targets:
                        if _missing(t, groups):
                            out.append(_orphan("router", f"router_settings.{name}", t,
                                               {"setting": name, "primary": primary, "dangling": t}))
        elif name == "default_fallbacks":
            if not isinstance(data, list):
                continue
            for t in data:
                if _missing(t, groups):
                    out.append(_orphan("router", "router_settings.default_fallbacks", t,
                                       {"setting": "default_fallbacks", "dangling": t}))
        elif name == "model_group_alias":
            if not isinstance(data, dict):
                continue
            for alias, target in data.items():
                if _missing(target, groups):
                    out.append(_orphan("router", "router_settings.model_group_alias", target,
                                       {"setting": "model_group_alias", "alias": alias, "dangling": target}))
    return out


def key_orphans(keys: list[dict], groups: set[str]) -> list[dict]:
    """Per key: models[] entries not in G and not one of the key's own alias names;
    alias targets not in G. An empty models list means 'all allowed' — never an orphan."""
    out: list[dict] = []
    for k in keys or []:
        token = k.get("token")
        label = k.get("key_alias") or (token or "")[:10]
        aliases = k.get("aliases") if isinstance(k.get("aliases"), dict) else {}
        alias_names = set(aliases.keys())
        for m in (k.get("models") or []):
            if _missing(m, groups) and m not in alias_names:
                out.append(_orphan("key", f"key '{label}' → allowed models", m,
                                   {"token": token, "field": "models", "entry": m}))
        for alias_name, target in aliases.items():
            if _missing(target, groups):
                out.append(_orphan("key", f"key '{label}' → alias '{alias_name}'", target,
                                   {"token": token, "field": "aliases", "entry": alias_name, "dangling": target}))
    return out


def trim_router_setting(value, target: dict):
    """Return `value` with the dangling piece removed, at the right granularity."""
    setting = target["setting"]
    if setting in _FALLBACK_RULE_SETTINGS:
        primary, dangling = target["primary"], target["dangling"]
        out = []
        for rule in (value or []):
            if not isinstance(rule, dict) or primary not in rule:
                out.append(rule); continue
            if dangling == primary:
                continue                              # drop the whole rule
            trimmed = [t for t in rule[primary] if t != dangling]
            if trimmed:
                out.append({**rule, primary: trimmed})
            # else: empty target list → drop the rule
        return out
    if setting == "default_fallbacks":
        return [t for t in (value or []) if t != target["dangling"]]
    if setting == "model_group_alias":
        return {a: t for a, t in (value or {}).items() if a != target["alias"]}
    return value


def trim_key_field(value, target: dict):
    """Return the key's models list / aliases dict with the dead entry removed."""
    if target["field"] == "models":
        return [m for m in (value or []) if m != target["entry"]]
    return {a: t for a, t in (value or {}).items() if a != target["entry"]}
