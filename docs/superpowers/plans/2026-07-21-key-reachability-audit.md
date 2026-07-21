# Key Reachability / Fallback Collision Audit (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An **advisory** audit that reports every cross-group fallback path (a key can reach a model group outside its allow-list because LiteLLM applies fallbacks without re-checking the per-key ACL, matching on a deployment's provider-stripped base model), plus a per-key "can also reach…" preview — and fold `model_group_alias` names into `G` to fix a dormant Phase-1 false-positive.

**Architecture:** One pure engine (`reachability.py`) mirrors LiteLLM's fallback matcher (parse/strip against a vendored `provider_list` snapshot) and computes collisions + per-key over-reach from the config store's cleartext model strings. A read-only `GET /api/config/reachability` endpoint and advisory UI surfaces (Routing panel subsection, Keys editor line) consume it. **Nothing gates** — no Apply block, no key-save rejection.

**Tech Stack:** FastAPI + asyncpg (backend; `ui/.venv/bin/python -m pytest`); Svelte 5 runes + Vite (`cd ui/frontend && npm run build`). NEVER use system `python3` for tests — always `ui/.venv/bin/python`.

## Global Constraints

- **Advisory only.** No collision ever blocks Apply or a key save. A test must prove Apply succeeds with a collision present.
- **Match semantics (verified live, litellm 1.89.2):** `get_llm_provider` consumes the FIRST known-provider prefix (`groq/openai/gpt-oss-20b` → parsed `openai/gpt-oss-20b`, provider `groq`); `_check_stripped_model_group` strips ONE more provider prefix (parsed `openai/gpt-oss-20b` → stripped `gpt-oss-20b`). A string with no known-provider prefix parses unchanged (`gpt-4o` → parsed `gpt-4o`, stripped None). The matcher's tiers: exact (the group name), then stripped, then `"*"`.
- **`SEMANTICS_VERSION = "1.89.2"`** surfaced in the API payload and UI; the vendored `PROVIDER_LIST` is a frozen snapshot of `litellm.provider_list` (141 entries as of 1.89.2) with a recorded source version.
- **Cross-group failure candidates** `C(g) = {g} ∪ ⋃ match_candidates(d)` over g's deployments (group name included because the exact tier fires on it). Collision iff a fallback primary ∈ C(g) and its targets ⊄ {g}.
- **`G`** = distinct `model_name` across effective (applied⊕staged, non-deleted) model items, **now optionally folded with `model_group_alias` names**.
- Malformed/unparsable model strings → skipped (return None), never raise. Key-store failure → `{"error":"query_failed", collisions:[...]}` (collisions preserved). All new endpoints `login_required`.
- Backend suite baseline: 280 passed / 1 skipped. Keep green.

---

### Task 1: `model_group_alias` names fold into `G` (Phase-1 fix, TDD)

**Files:**
- Modify: `ui/app/config_integrity.py` (`group_names` gains `mga_names` param)
- Modify: `ui/app/config_engine.py:65`, `ui/app/routes/config_v3_routes.py:220`, `ui/app/routes/keys_routes.py:32` (pass mga names)
- Modify: `ui/tests/test_config_integrity.py`

**Interfaces:**
- Produces: `group_names(model_items, mga_names: set[str] | None = None) -> set[str]`; helper `mga_names_from(router_items) -> set[str]` in config_integrity.

- [ ] **Step 1: Write failing tests** — append to `ui/tests/test_config_integrity.py`:

```python
from app.config_integrity import mga_names_from

def test_group_names_folds_mga_names():
    models = [_model("id1", "gpt-oss-20b-1x")]
    assert group_names(models, mga_names={"friendly"}) == {"gpt-oss-20b-1x", "friendly"}

def test_mga_names_from_extracts_keys():
    ri = [_rs("model_group_alias", {"friendly": "gpt-oss-20b-1x", "other": "x"})]
    assert mga_names_from(ri) == {"friendly", "other"}
    assert mga_names_from([_rs("model_group_alias", "not-a-dict")]) == set()
    assert mga_names_from([]) == set()

def test_fallback_referencing_mga_name_not_orphan():
    # a fallback whose primary is an mga NAME (not a model group) must not be flagged
    G2 = group_names([_model("id1", "real")], mga_names=mga_names_from([_rs("model_group_alias", {"friendly": "real"})]))
    assert router_orphans([_rs("fallbacks", [{"friendly": ["real"]}])], G2) == []
```

- [ ] **Step 2: Verify RED** — `cd ui && .venv/bin/python -m pytest tests/test_config_integrity.py -k "mga or folds" -q` → FAIL (`mga_names_from` undefined; `group_names` takes 1 arg).

- [ ] **Step 3: Implement** — in `ui/app/config_integrity.py`:

Change `group_names`:
```python
def group_names(model_items: list[dict], mga_names: set | None = None) -> set:
    """Distinct public model_name across non-deleted model items (effective),
    optionally folded with model_group_alias names (which are also valid public
    names a fallback/key may legitimately reference)."""
    names = {(it.get("data") or {}).get("model_name")
             for it in model_items
             if it.get("kind") == "model" and it.get("flag") != "deleted"
             and (it.get("data") or {}).get("model_name")}
    return names | (mga_names or set())
```
Add:
```python
def mga_names_from(router_items: list[dict]) -> set:
    """The set of model_group_alias names (its dict keys) across router items."""
    out = set()
    for it in router_items:
        if it.get("name") == "model_group_alias" and isinstance(it.get("data"), dict):
            out |= set(it["data"].keys())
    return out
```
Then update the three consumers to pass mga names (each already builds `eff`):
- `config_engine.py:65` — after `_groups = group_names(...)`, change to:
```python
    _model_items = [it for it in eff if it["kind"] == "model"]
    _router_items = [it for it in eff if it["kind"] == "router_setting" and it.get("flag") != "deleted"]
    _groups = group_names(_model_items, mga_names_from(_router_items))
    _orphans = router_orphans(_router_items, _groups)
```
  (add `mga_names_from` to the existing `from app.config_integrity import ...` line.)
- `config_v3_routes.py:220` (in `config_integrity`) — mirror it: build `router_items` once, `groups = group_names([i for i in eff if i["kind"]=="model"], mga_names_from(router_items))`, and reuse `router_items` for `router_orphans`. Add `mga_names_from` to its config_integrity import.
- `keys_routes.py:32` (in `_validate_key_refs`) — `groups = group_names([i for i in eff if i["kind"]=="model"], mga_names_from([i for i in eff if i["kind"]=="router_setting"]))`. Add `mga_names_from` to its import.

- [ ] **Step 4: Verify GREEN** — `cd ui && .venv/bin/python -m pytest tests/ -q` → all pass (283/1: +3 new). No regressions (existing `group_names` calls still valid — new param defaults to None).

- [ ] **Step 5: Commit**
```bash
git add ui/app/config_integrity.py ui/app/config_engine.py ui/app/routes/config_v3_routes.py ui/app/routes/keys_routes.py ui/tests/test_config_integrity.py
git commit -m "fix: fold model_group_alias names into G so referencing them isn't a false orphan"
```

---

### Task 2: `reachability.py` — parse/strip + collision + over-reach (TDD)

**Files:**
- Create: `ui/app/reachability.py`
- Create: `ui/tests/test_reachability.py`

**Interfaces:**
- Produces: `PROVIDER_LIST: frozenset`, `SEMANTICS_VERSION: str`, `parse_base(model_str) -> dict|None` (`{"provider","parsed","stripped"}`), `match_candidates(deployment_model) -> set`, `collision_audit(model_items, router_items) -> list[dict]`, `key_over_reach(keys, collisions, groups, mga) -> list[dict]`.

- [ ] **Step 1: Write failing tests** — create `ui/tests/test_reachability.py`:

```python
from app.reachability import (parse_base, match_candidates, collision_audit,
                              key_over_reach, SEMANTICS_VERSION, PROVIDER_LIST)

def _model(mid, mname, model_str, flag=None):
    it = {"kind": "model", "name": mid,
          "data": {"model_name": mname, "model_info": {"id": mid},
                   "litellm_params": {"model": model_str}}}
    if flag: it["flag"] = flag
    return it

def _rs(name, data):
    return {"kind": "router_setting", "name": name, "data": data}

# ── parse_base (verified live against litellm 1.89.2 get_llm_provider) ──
def test_parse_incident_double_prefix():
    assert parse_base("groq/openai/gpt-oss-20b") == {"provider": "groq", "parsed": "openai/gpt-oss-20b", "stripped": "gpt-oss-20b"}

def test_parse_single_prefix():
    assert parse_base("deepinfra/Qwen/Qwen3.6-27B") == {"provider": "deepinfra", "parsed": "Qwen/Qwen3.6-27B", "stripped": None}

def test_parse_hosted_vllm_single():
    r = parse_base("hosted_vllm/qwen3.6-27b")
    assert r["provider"] == "hosted_vllm" and r["parsed"] == "qwen3.6-27b" and r["stripped"] is None

def test_parse_zero_prefix():
    assert parse_base("gpt-4o") == {"provider": None, "parsed": "gpt-4o", "stripped": None}

def test_parse_unknown_prefix_kept():
    # "notaprovider/" is not in PROVIDER_LIST → nothing stripped
    assert parse_base("notaprovider/foo")["parsed"] == "notaprovider/foo"

def test_parse_malformed_none():
    for bad in (None, "", 123, {"x": 1}):
        assert parse_base(bad) is None

def test_semantics_version_and_list():
    assert SEMANTICS_VERSION == "1.89.2" and "groq" in PROVIDER_LIST and len(PROVIDER_LIST) >= 100

# ── match_candidates ──
def test_match_candidates_incident():
    assert match_candidates("groq/openai/gpt-oss-20b") == {"openai/gpt-oss-20b", "gpt-oss-20b"}

def test_match_candidates_zero_prefix():
    assert match_candidates("gpt-4o") == {"gpt-4o"}

def test_match_candidates_malformed_empty():
    assert match_candidates(None) == set()

# ── collision_audit ──
def test_collision_incident_stripped_match():
    # hindsight-llm holds groq/openai/gpt-oss-20b; fallback keyed gpt-oss-20b -> deepinfra
    models = [_model("d1", "hindsight-llm", "groq/openai/gpt-oss-20b")]
    router = [_rs("fallbacks", [{"gpt-oss-20b": ["gpt-oss-20b-deepinfra"]}])]
    c = collision_audit(models, router)
    assert len(c) == 1
    assert c[0]["group"] == "hindsight-llm" and c[0]["fallback_key"] == "gpt-oss-20b"
    assert c[0]["targets"] == ["gpt-oss-20b-deepinfra"] and c[0]["deployment_id"] == "d1"
    assert c[0]["base_model"] == "groq/openai/gpt-oss-20b"

def test_collision_exact_group_name_match():
    # a normal fallback {qwen-2x:[deepinfra]} — exact tier fires on the group name itself
    models = [_model("d1", "qwen3.6-27b-2x", "hosted_vllm/qwen3.6-27b")]
    router = [_rs("fallbacks", [{"qwen3.6-27b-2x": ["qwen3.6-27b-deepinfra"]}])]
    c = collision_audit(models, router)
    assert len(c) == 1 and c[0]["group"] == "qwen3.6-27b-2x" and c[0]["deployment_id"] is None
    assert c[0]["targets"] == ["qwen3.6-27b-deepinfra"]

def test_no_collision_when_targets_within_group():
    models = [_model("d1", "g", "openai/gpt-oss-20b")]
    router = [_rs("fallbacks", [{"gpt-oss-20b": ["g"]}])]  # target is the same group
    assert collision_audit(models, router) == []

def test_collision_default_fallbacks():
    models = [_model("d1", "g", "openai/gpt-4o")]
    router = [_rs("default_fallbacks", ["backup-group"])]
    c = collision_audit(models, router)
    assert c and c[0]["targets"] == ["backup-group"] and c[0]["fallback_setting"] == "default_fallbacks"

def test_collision_dedup_and_deleted_skipped():
    models = [_model("d1", "g", "groq/openai/gpt-oss-20b"),
              _model("d2", "gone", "groq/openai/gpt-oss-20b", flag="deleted")]
    router = [_rs("fallbacks", [{"gpt-oss-20b": ["t"]}])]
    c = collision_audit(models, router)
    assert len(c) == 1 and c[0]["group"] == "g"   # deleted deployment ignored

# ── key_over_reach ──
def _collision(group, targets, via="gpt-oss-20b"):
    return {"group": group, "deployment_id": "d1", "base_model": "x",
            "fallback_setting": "fallbacks", "fallback_key": via, "targets": targets}

def test_over_reach_names_extra_target():
    cols = [_collision("hindsight-llm", ["gpt-oss-20b-deepinfra"])]
    keys = [{"token": "h1", "key_alias": "hindsight", "models": ["hindsight-llm"], "aliases": {}}]
    o = key_over_reach(keys, cols, groups={"hindsight-llm", "gpt-oss-20b-deepinfra"}, mga={})
    assert len(o) == 1 and o[0]["key_alias"] == "hindsight"
    assert o[0]["extra"] == [{"target": "gpt-oss-20b-deepinfra", "via_group": "hindsight-llm", "via_fallback": "gpt-oss-20b"}]

def test_over_reach_none_when_target_already_allowed():
    cols = [_collision("g", ["t"])]
    keys = [{"token": "h1", "key_alias": "k", "models": ["g", "t"], "aliases": {}}]
    assert key_over_reach(keys, cols, groups={"g", "t"}, mga={}) == []

def test_over_reach_empty_models_is_all_no_extra():
    cols = [_collision("g", ["t"])]
    keys = [{"token": "h1", "key_alias": "k", "models": [], "aliases": {}}]  # unrestricted = all
    assert key_over_reach(keys, cols, groups={"g", "t"}, mga={}) == []

def test_over_reach_special_all_proxy_is_all():
    cols = [_collision("g", ["t"])]
    keys = [{"token": "h1", "key_alias": "k", "models": ["all-proxy-models"], "aliases": {}}]
    assert key_over_reach(keys, cols, groups={"g", "t"}, mga={}) == []

def test_over_reach_resolves_alias_and_mga():
    cols = [_collision("real-group", ["t"])]
    # key allows alias name 'myalias' -> resolves to 'real-group' via mga
    keys = [{"token": "h1", "key_alias": "k", "models": ["myalias"], "aliases": {}}]
    o = key_over_reach(keys, cols, groups={"real-group", "t", "myalias"}, mga={"myalias": "real-group"})
    assert len(o) == 1 and o[0]["extra"][0]["target"] == "t"
```

- [ ] **Step 2: Verify RED** — `cd ui && .venv/bin/python -m pytest tests/test_reachability.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `ui/app/reachability.py`**

```python
from __future__ import annotations

SEMANTICS_VERSION = "1.89.2"

# Frozen snapshot of litellm.provider_list (1.89.2). Re-validated against the live
# container at integration time (see plan Task 5). Sorted for readability.
PROVIDER_LIST = frozenset({
    "a2a", "a2a_agent", "ai21", "ai21_chat", "aiml", "aiohttp_openai", "amazon_nova",
    "anthropic", "anthropic_text", "apertis", "assemblyai", "auto_router", "aws_polly",
    "azure", "azure_ai", "azure_text", "baseten", "bedrock", "bedrock_mantle",
    "black_forest_labs", "bytez", "cerebras", "charity_engine", "chatgpt", "chutes",
    "clarifai", "cloudflare", "codestral", "cohere", "cohere_chat", "cometapi",
    "compactifai", "cursor", "custom", "custom_openai", "dashscope", "databricks",
    "datarobot", "deepgram", "deepinfra", "deepseek", "docker_model_runner", "dotprompt",
    "elevenlabs", "empower", "fal_ai", "featherless_ai", "fireworks_ai", "friendliai",
    "galadriel", "gemini", "gigachat", "github", "github_copilot", "gradient_ai", "groq",
    "helicone", "heroku", "hosted_vllm", "huggingface", "humanloop", "hyperbolic",
    "inception", "infinity", "jina_ai", "lambda_ai", "langflow", "langfuse", "langgraph",
    "lemonade", "litellm_agent", "litellm_proxy", "llamafile", "lm_studio", "manus",
    "maritalk", "meta_llama", "milvus", "minimax", "mistral", "moonshot", "morph",
    "nano-gpt", "nebius", "neosantara", "nlp_cloud", "novita", "nscale", "nvidia_nim",
    "nvidia_riva", "oci", "ollama", "ollama_chat", "oobabooga", "openai", "openai_like",
    "openrouter", "ovhcloud", "perplexity", "petals", "pg_vector", "poe", "predibase",
    "publicai", "ragflow", "recraft", "reducto", "replicate", "runwayml", "s3_vectors",
    "sagemaker", "sagemaker_chat", "sagemaker_nova", "sambanova", "sap", "scaleway",
    "snowflake", "soniox", "stability", "synthetic", "tensormesh",
    "text-completion-codestral", "text-completion-inception", "text-completion-openai",
    "together_ai", "topaz", "triton", "v0", "vercel_ai_gateway", "vertex_ai",
    "vertex_ai_beta", "vllm", "volcengine", "voyage", "wandb", "watsonx", "watsonx_text",
    "xai", "xiaomi_mimo", "xinference", "zai",
})

_SPECIAL_ALL = {"all-proxy-models", "all-team-models"}


def _strip_one(s):
    """Remove a single leading 'provider/' prefix if the provider is known. Returns
    (remainder, provider) or (s, None)."""
    if "/" in s:
        head, rest = s.split("/", 1)
        if head in PROVIDER_LIST:
            return rest, head
    return s, None


def parse_base(model_str):
    """Mirror get_llm_provider (consume first known-provider prefix) +
    _check_stripped_model_group (strip one MORE known prefix). Returns
    {"provider","parsed","stripped"} or None for malformed input."""
    if not isinstance(model_str, str) or not model_str:
        return None
    parsed, provider = _strip_one(model_str)
    stripped, _ = _strip_one(parsed)
    return {"provider": provider, "parsed": parsed, "stripped": stripped if stripped != parsed else None}


def match_candidates(deployment_model):
    """Deployment-derived strings the fallback matcher can see on failure."""
    pb = parse_base(deployment_model)
    if pb is None:
        return set()
    out = {pb["parsed"]}
    if pb["stripped"]:
        out.add(pb["stripped"])
    return out


_FALLBACK_SETTINGS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")


def _deployments_by_group(model_items):
    groups = {}
    for it in model_items:
        if it.get("kind") != "model" or it.get("flag") == "deleted":
            continue
        data = it.get("data") or {}
        g = data.get("model_name")
        if not g:
            continue
        mid = (data.get("model_info") or {}).get("id") or it.get("name")
        model_str = (data.get("litellm_params") or {}).get("model")
        groups.setdefault(g, []).append((mid, model_str))
    return groups


def collision_audit(model_items, router_items):
    """Cross-group fallback paths. See spec §1. Advisory — never gates."""
    by_group = _deployments_by_group(model_items)
    # rules: list of (setting, primary, targets). default_fallbacks: primary=None (any failure).
    rules = []
    for it in router_items:
        name, data = it.get("name"), it.get("data")
        if name in _FALLBACK_SETTINGS and isinstance(data, list):
            for rule in data:
                if isinstance(rule, dict):
                    for primary, targets in rule.items():
                        if isinstance(targets, list):
                            rules.append((name, primary, targets))
        elif name == "default_fallbacks" and isinstance(data, list):
            rules.append(("default_fallbacks", None, data))

    out, seen = [], set()
    for g, deps in by_group.items():
        # candidate → the deployment_id that produced it (None = the group name, exact tier)
        cand = {g: None}
        for mid, model_str in deps:
            for c in match_candidates(model_str):
                cand.setdefault(c, mid)
        for setting, primary, targets in rules:
            # default_fallbacks (primary None) fires from ANY failure in g
            hit = (primary is None) or (isinstance(primary, str) and primary in cand)
            if not hit:
                continue
            extra_targets = [t for t in targets if isinstance(t, str) and t != g]
            if not extra_targets:
                continue
            dep_id = None if primary is None else cand.get(primary)
            key = (g, dep_id, setting, primary)
            if key in seen:
                continue
            seen.add(key)
            base = next((ms for mid, ms in deps if mid == dep_id), None)
            out.append({"group": g, "deployment_id": dep_id, "base_model": base,
                        "fallback_setting": setting, "fallback_key": primary,
                        "targets": extra_targets})
    return out


def _allowed_groups(models, aliases, groups, mga):
    """Resolve a key's models[] to concrete group names. Empty = all; specials =
    all; alias names / mga names resolve; unknown literals kept (may be a group)."""
    if not models:
        return set(groups)
    alias_names = set(aliases.keys()) if isinstance(aliases, dict) else set()
    allowed = set()
    for m in models:
        if not isinstance(m, str):
            continue
        if m in _SPECIAL_ALL:
            return set(groups)
        if m == "no-default-models":
            continue
        if m in alias_names:            # per-key alias name → resolve its target
            tgt = aliases.get(m)
            if isinstance(tgt, str):
                allowed.add(tgt)
            continue
        if m in mga:                    # model_group_alias name → resolve
            allowed.add(mga[m]); continue
        allowed.add(m)
    return allowed


def key_over_reach(keys, collisions, groups, mga):
    """Per key, the groups reachable via fallbacks beyond its allow-list. Pure join."""
    out = []
    for k in keys or []:
        allowed = _allowed_groups(k.get("models") or [], k.get("aliases") or {}, groups, mga)
        extra = []
        seen = set()
        for c in collisions:
            if c["group"] not in allowed:
                continue
            for t in c["targets"]:
                if t in allowed or t in seen:
                    continue
                seen.add(t)
                extra.append({"target": t, "via_group": c["group"], "via_fallback": c["fallback_key"]})
        if extra:
            out.append({"key_alias": k.get("key_alias") or (k.get("token") or "")[:10],
                        "token": k.get("token"), "extra": extra})
    return out
```

- [ ] **Step 4: Verify GREEN** — `cd ui && .venv/bin/python -m pytest tests/test_reachability.py -q` → all pass. Full suite → no regressions.

- [ ] **Step 5: Commit**
```bash
git add ui/app/reachability.py ui/tests/test_reachability.py
git commit -m "feat: reachability engine — parse/strip mirror + collision audit + per-key over-reach"
```

---

### Task 3: `GET /api/config/reachability` endpoint (TDD)

**Files:**
- Modify: `ui/app/routes/config_v3_routes.py`
- Modify: `ui/tests/test_config_v3_routes.py`

**Interfaces:**
- Consumes: `collision_audit`, `key_over_reach`, `SEMANTICS_VERSION` (Task 2); `group_names`, `mga_names_from` (Task 1); `effective`, `make_config_store`, `make_keys_client` (existing).
- Produces: `GET /api/config/reachability` → `{semantics_version, collisions, key_over_reach}` or `{error:"query_failed", detail, collisions, key_over_reach:[]}`.

- [ ] **Step 1: Write failing tests** — append to `ui/tests/test_config_v3_routes.py` (reuses `_client`, and a model-bearing store):

```python
class FakeStoreReach(FakeStore):
    def __init__(self):
        super().__init__()
        self._applied = [
            {"kind": "model", "name": "d1", "data": {"model_name": "hindsight-llm",
             "model_info": {"id": "d1"}, "litellm_params": {"model": "groq/openai/gpt-oss-20b"}}},
            {"kind": "router_setting", "name": "fallbacks", "data": [{"gpt-oss-20b": ["gpt-oss-20b-deepinfra"]}]},
        ]
        self._staged = []

class FakeKeysReach:
    def __init__(self, keys): self._keys = keys
    async def list_keys(self): return self._keys

def test_reachability_reports_collision_and_over_reach(tmp_path):
    keys = [{"token": "h1", "key_alias": "hindsight", "models": ["hindsight-llm"], "aliases": {}}]
    c = _client(tmp_path, FakeStoreReach())
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: FakeKeysReach(keys)
    d = c.get("/api/config/reachability").json()
    assert d["semantics_version"] == "1.89.2"
    assert any(x["group"] == "hindsight-llm" for x in d["collisions"])
    assert d["key_over_reach"][0]["extra"][0]["target"] == "gpt-oss-20b-deepinfra"

def test_reachability_key_store_failure_preserves_collisions(tmp_path):
    class Boom:
        async def list_keys(self): raise RuntimeError("down")
    c = _client(tmp_path, FakeStoreReach())
    import app.routes.config_v3_routes as cr
    cr.make_keys_client = lambda: Boom()
    d = c.get("/api/config/reachability").json()
    assert d["error"] == "query_failed" and d["collisions"] and d["key_over_reach"] == []

def test_reachability_requires_login(tmp_path):
    c = _client(tmp_path, FakeStoreReach()); c.cookies.clear()
    assert c.get("/api/config/reachability").status_code == 401
```

- [ ] **Step 2: Verify RED** — `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -k reachability -q` → FAIL (404).

- [ ] **Step 3: Implement** — in `ui/app/routes/config_v3_routes.py`, add to the reachability import and add the endpoint near `/config/integrity`:

Add import:
```python
from app.reachability import collision_audit, key_over_reach, SEMANTICS_VERSION
```
Add endpoint:
```python
@router.get("/config/reachability", dependencies=[Depends(login_required)])
async def config_reachability():
    store = make_config_store()
    eff = effective(await store.applied(), await store.staged())
    model_items = [i for i in eff if i["kind"] == "model"]
    router_items = [i for i in eff if i["kind"] == "router_setting" and i.get("flag") != "deleted"]
    collisions = collision_audit(model_items, router_items)
    groups = group_names(model_items, mga_names_from(router_items))
    mga_item = next((i for i in router_items if i["name"] == "model_group_alias"), None)
    mga = mga_item["data"] if (mga_item and isinstance(mga_item.get("data"), dict)) else {}
    try:
        keys = await make_keys_client().list_keys()
    except Exception as e:
        return {"error": "query_failed", "detail": str(e), "semantics_version": SEMANTICS_VERSION,
                "collisions": collisions, "key_over_reach": []}
    return {"semantics_version": SEMANTICS_VERSION, "collisions": collisions,
            "key_over_reach": key_over_reach(keys, collisions, groups, mga)}
```
(`group_names`/`mga_names_from` are already imported here from Task 1.)

- [ ] **Step 4: Verify GREEN** — `cd ui && .venv/bin/python -m pytest tests/test_config_v3_routes.py -q` → pass. Full suite → no regressions.

- [ ] **Step 5: Commit**
```bash
git add ui/app/routes/config_v3_routes.py ui/tests/test_config_v3_routes.py
git commit -m "feat: GET /api/config/reachability — advisory collision + per-key over-reach report"
```

---

### Task 4: Frontend — Routing advisory subsection + Keys editor line

**Files:**
- Modify: `ui/frontend/src/lib/api.js`
- Modify: `ui/frontend/src/routes/Routing.svelte`
- Modify: `ui/frontend/src/routes/Keys.svelte`

**Interfaces:**
- Consumes: `GET /api/config/reachability` (Task 3).

- [ ] **Step 1: api.js** — add next to `integrity`:
```js
  reachability: () => req('/api/config/reachability'),
```

- [ ] **Step 2: Routing.svelte** — add state + loader after the integrity block (near line 97):
```svelte
  let reach = $state(null)
  let reachErr = $state('')
  async function loadReach() {
    try { reach = await api.reachability(); reachErr = reach?.error ? 'Reachability check failed (key API).' : '' }
    catch (e) { reachErr = e.message }
  }
  onMount(loadReach)
  let reachCount = $derived((reach?.collisions?.length || 0) + (reach?.key_over_reach?.length || 0))
```
Add the panel markup immediately after the integrity `</section>` (line 194):
```svelte
  <section class="card">
    <h2>Reachability (advisory)
      {#if reachCount > 0}<span class="badge-info">{reachCount}</span>{/if}
      {#if reach?.semantics_version}<span class="caption">semantics: LiteLLM {reach.semantics_version}</span>{/if}</h2>
    {#if reachErr}<div class="banner err">{reachErr}</div>
    {:else if !reach}<p class="hint">Checking…</p>
    {:else if reachCount === 0}<p class="hint">✓ No cross-group fallback paths.</p>
    {:else}
      <p class="hint">Fallbacks can route a failed request to a group the caller wasn't granted (LiteLLM applies fallbacks without re-checking per-key access). This is informational — to remove a path, grant the key the target group or re-scope the fallback.</p>
      <ul class="orphans">
        {#each reach.key_over_reach as k (k.token)}
          {#each k.extra as e (e.target + e.via_group)}
            <li><span class="mono">key {k.key_alias}</span> can also reach <span class="mono amber">{e.target}</span> (via <span class="mono">{e.via_group}</span> → fallback <span class="mono">{e.via_fallback}</span>)</li>
          {/each}
        {/each}
        {#each reach.collisions as c (c.group + c.fallback_key + (c.deployment_id||''))}
          <li><span class="mono">{c.group}</span>{#if c.base_model} (deployment <span class="mono">{c.base_model}</span>){/if} → can route to <span class="mono amber">{c.targets.join(', ')}</span> via fallback <span class="mono">{c.fallback_key}</span></li>
        {/each}
      </ul>
    {/if}
  </section>
```
Add styles (near the Phase-1 `.badge-warn`):
```svelte
  .badge-info{background:#e5effb;color:#0a4a8f;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:600;margin-left:8px}
  .caption{font-size:11px;color:#6e6e73;font-weight:400;margin-left:8px}
  .amber{color:#9a5b00}
```

- [ ] **Step 3: Keys.svelte** — add a reachability lookup and a passive line in the editor. In `<script>` (after `onMount(load)` at line 53):
```svelte
  let overReach = $state([])
  async function loadReach() {
    try { const r = await api.reachability(); overReach = r?.key_over_reach || [] } catch { overReach = [] }
  }
  onMount(loadReach)
  let editReach = $derived(editingToken ? (overReach.find(k => k.token === editingToken)?.extra || []) : [])
```
In the editor form, right after the Models `<label>…</label>` block (the multi-select around line 159-163 — insert after its closing `</label>`):
```svelte
        {#if editReach.length}
          <p class="reach-note">⚠ Via fallbacks this key can also reach: {editReach.map(e => e.target).join(', ')}. Informational — see Routing → Reachability.</p>
        {/if}
```
Add style:
```svelte
  .reach-note{font-size:12px;color:#9a5b00;background:#fff4e5;border-radius:8px;padding:6px 10px;margin:4px 0}
```

- [ ] **Step 4: Build** — `cd ui/frontend && npm run build` → `✓ built`, no errors.

- [ ] **Step 5: Commit**
```bash
git add ui/frontend/src/lib/api.js ui/frontend/src/routes/Routing.svelte ui/frontend/src/routes/Keys.svelte
git commit -m "feat(ui): advisory reachability panel + per-key can-also-reach note"
```

---

### Task 5: Docs, integration (incl. live cross-validation), release, deploy (controller)

**Files:**
- Modify: `docs/admin-ui-guide.md` (Reachability subsection + upgrade re-validation note).

- [ ] **Step 1: Docs** — add under the Routing section, after the Phase-1 "Referential integrity" subsection:
```markdown
### Reachability (advisory)

Because LiteLLM applies router fallbacks **without re-checking a key's allowed
models**, and its fallback matcher keys on a deployment's provider-stripped base
model, a key can end up served by a model group it was never granted. This
subsection lists every such path:

- **Collisions** — a failure in group *G* (via a deployment whose stripped base
  model, or *G*'s own name, matches a fallback primary) can route to targets
  outside *G*.
- **Per-key over-reach** — "key *K* can also reach *X* (via *G* → fallback *F*)".

This is **advisory only** — it never blocks Apply or key saves, because a
fallback-reachable target is often intentional. To close a path you don't want,
either grant the key the target group or re-scope/remove the fallback. Results
are computed for a specific LiteLLM version (shown as "semantics: LiteLLM
x.y.z"); **after upgrading LiteLLM, re-run the cross-validation harness** (see
below) to confirm the vendored provider list still matches.
```

- [ ] **Step 2: Full suite + build** — `cd ui && .venv/bin/python -m pytest tests/ -q` (expect ~289/1) and `cd ui/frontend && npm run build`.

- [ ] **Step 3: Live cross-validation (the fidelity gate)** — bring up the local hybrid stack (recreate `docker-compose.override.yml` with `STORE_MODEL_IN_DB: "true"` + `build: ./ui`; `docker compose up -d --build llm-proxy-ui`; wait healthy). Then diff `parse_base` against the live `get_llm_provider` for a fixed model-string set that includes every real `.75` deployment base + the fixtures:
```bash
# expected pairs computed by our parser, verified against the live container
docker exec litellm-proxy python3 -c '
import litellm, json
ours = {
 "groq/openai/gpt-oss-20b": "openai/gpt-oss-20b",
 "deepinfra/Qwen/Qwen3.6-27B": "Qwen/Qwen3.6-27B",
 "hosted_vllm/qwen3.6-27b": "qwen3.6-27b",
 "hosted_vllm/openai/gpt-oss-20b": "openai/gpt-oss-20b",
 "deepinfra/openai/gpt-oss-20b": "openai/gpt-oss-20b",
 "gpt-4o": "gpt-4o",
}
bad = []
for m, expect_parsed in ours.items():
    model, prov, *_ = litellm.get_llm_provider(m)
    if model != expect_parsed: bad.append((m, model, expect_parsed))
print("DIVERGENCE:", bad if bad else "NONE — parse_base matches live get_llm_provider")
'
```
Expect `NONE`. If any divergence, STOP — the vendored provider list or strip algorithm needs updating before ship.

- [ ] **Step 4: Integration on the stack** — seed the incident topology into `ui_config_applied` (a `hindsight-llm` model with `litellm_params.model = groq/openai/gpt-oss-20b` + a `fallbacks` row `[{"gpt-oss-20b":["gpt-oss-20b-deepinfra"]}]`) and a restricted key allowed only `hindsight-llm` (via the litellm master key). Drive the authed API: `GET /api/config/reachability` → asserts a collision on `hindsight-llm` and `key_over_reach` naming `gpt-oss-20b-deepinfra`; `POST /api/apply` still succeeds (advisory proven — Apply not blocked by the collision, only by any Phase-1 orphan which this seed avoids). Browser-check the Routing panel shows the advisory rows and the Keys editor shows the note. Clean up seeds + `docker compose down && rm docker-compose.override.yml`.

- [ ] **Step 5: Final whole-branch review** (opus) → fix Critical/Important → finishing-a-development-branch: merge `--no-ff` to main (CI cuts the next minor), pull the release commit, bump the UI image pin, deploy to `.75` UI-only (litellm `StartedAt` unchanged — UI-only, no router-settings write), update memory.

---

## Self-Review

**Spec coverage:** parse/strip mirror with SEMANTICS_VERSION + vendored PROVIDER_LIST (T2) ✓; collision_audit incl. exact-tier group-name match + default_fallbacks + dedup + deleted-skip (T2) ✓; key_over_reach pure join incl. specials/empty/alias/mga resolution (T2) ✓; mga-name fold into G across all Phase-1 consumers (T1) ✓; read-only login-gated endpoint w/ query_failed preserving collisions (T3) ✓; advisory Routing panel (no Fix) + Keys note + semantics caption (T4) ✓; docs + live cross-validation gate + incident-topology integration proving Apply-not-blocked (T5) ✓; advisory-only enforced (no gate anywhere; T5 asserts Apply succeeds) ✓.

**Placeholder scan:** none — full code in every code step; PROVIDER_LIST is the verified 141-entry snapshot; cross-validation uses concrete expected pairs.

**Type consistency:** `parse_base`→`{provider,parsed,stripped}`, collision record `{group,deployment_id,base_model,fallback_setting,fallback_key,targets}`, over-reach `{key_alias,token,extra:[{target,via_group,via_fallback}]}` are produced in T2 and consumed unchanged by T3 endpoint + T4 UI; `group_names(model_items, mga_names)` new signature (T1) is used consistently in T3; `mga_names_from` defined T1, used T3.
