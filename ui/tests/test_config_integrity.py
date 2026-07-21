from app.config_integrity import (group_names, router_orphans, key_orphans,
                                   trim_router_setting, trim_key_field, mga_names_from)

def _model(name, mname, flag=None):
    it = {"kind": "model", "name": name, "data": {"model_name": mname}}
    if flag: it["flag"] = flag
    return it

def _rs(name, data, flag=None):
    it = {"kind": "router_setting", "name": name, "data": data}
    if flag: it["flag"] = flag
    return it

# ── group_names ─────────────────────────────────────────────────────────────
def test_group_names_dedups_and_skips_deleted():
    items = [_model("id1", "gpt-oss-20b-1x"), _model("id2", "gpt-oss-20b-1x"),
             _model("id3", "qwen3.6-27b-2x"), _model("id4", "gone", flag="deleted")]
    assert group_names(items) == {"gpt-oss-20b-1x", "qwen3.6-27b-2x"}

# ── router_orphans: fallbacks ───────────────────────────────────────────────
G = {"a", "b", "c"}
def test_fallback_primary_missing():
    o = router_orphans([_rs("fallbacks", [{"dead": ["a"]}])], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "fallbacks", "primary": "dead", "dangling": "dead"}

def test_fallback_target_missing():
    o = router_orphans([_rs("fallbacks", [{"a": ["b", "dead"]}])], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "fallbacks", "primary": "a", "dangling": "dead"}

def test_fallback_clean_no_orphans():
    assert router_orphans([_rs("fallbacks", [{"a": ["b"]}])], G) == []

def test_fallback_variants_scanned():
    for setting in ("context_window_fallbacks", "content_policy_fallbacks"):
        o = router_orphans([_rs(setting, [{"dead": ["a"]}])], G)
        assert len(o) == 1 and o[0]["target"]["setting"] == setting

def test_default_fallbacks_list_shape():
    o = router_orphans([_rs("default_fallbacks", ["a", "dead"])], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "default_fallbacks", "dangling": "dead"}

# ── router_orphans: model_group_alias ───────────────────────────────────────
def test_mga_target_missing():
    o = router_orphans([_rs("model_group_alias", {"myalias": "dead"})], G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"setting": "model_group_alias", "alias": "myalias", "dangling": "dead"}

def test_mga_alias_name_exempt():
    # the alias NAME is a new public name; only the target must exist
    assert router_orphans([_rs("model_group_alias", {"newname": "a"})], G) == []

def test_router_deleted_setting_skipped_by_caller_contract():
    # caller passes non-deleted only; checker still tolerant of a stray flag
    assert router_orphans([_rs("fallbacks", [{"dead": ["a"]}], flag="deleted")], G) == \
           router_orphans([_rs("fallbacks", [{"dead": ["a"]}])], G)  # checker ignores flag

def test_router_malformed_shapes_never_raise():
    assert router_orphans([_rs("fallbacks", "not-a-list")], G) == []
    assert router_orphans([_rs("model_group_alias", ["not", "a", "dict"])], G) == []
    assert router_orphans([_rs("fallbacks", [{"a": "not-a-list"}])], G) == []

# ── key_orphans ─────────────────────────────────────────────────────────────
def test_key_allowed_model_missing():
    keys = [{"token": "h1", "key_alias": "ci", "models": ["a", "dead"], "aliases": {}}]
    o = key_orphans(keys, G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"token": "h1", "field": "models", "entry": "dead"}

def test_key_alias_name_in_models_is_exempt():
    # #25281 injection: an alias NAME legitimately appears in models
    keys = [{"token": "h1", "key_alias": "ci", "models": ["a", "myalias"],
             "aliases": {"myalias": "a"}}]
    assert key_orphans(keys, G) == []

def test_key_alias_target_missing():
    keys = [{"token": "h1", "key_alias": "ci", "models": [], "aliases": {"gpt-4": "dead"}}]
    o = key_orphans(keys, G)
    assert len(o) == 1 and o[0]["reference"] == "dead"
    assert o[0]["target"] == {"token": "h1", "field": "aliases", "entry": "gpt-4", "dangling": "dead"}

def test_key_empty_models_means_all_allowed_no_orphan():
    assert key_orphans([{"token": "h1", "key_alias": "ci", "models": [], "aliases": {}}], G) == []

def test_key_malformed_never_raises():
    assert key_orphans([{"token": "h1", "models": None, "aliases": None}], G) == []

# ── trim helpers ────────────────────────────────────────────────────────────
def test_trim_router_drop_whole_rule_on_primary():
    v = [{"dead": ["a"]}, {"a": ["b"]}]
    assert trim_router_setting(v, {"setting": "fallbacks", "primary": "dead", "dangling": "dead"}) == [{"a": ["b"]}]

def test_trim_router_drop_only_target():
    v = [{"a": ["b", "dead"]}]
    assert trim_router_setting(v, {"setting": "fallbacks", "primary": "a", "dangling": "dead"}) == [{"a": ["b"]}]

def test_trim_router_empty_target_list_drops_rule():
    v = [{"a": ["dead"]}]
    assert trim_router_setting(v, {"setting": "fallbacks", "primary": "a", "dangling": "dead"}) == []

def test_trim_router_default_fallbacks_list():
    assert trim_router_setting(["a", "dead"], {"setting": "default_fallbacks", "dangling": "dead"}) == ["a"]

def test_trim_router_mga_entry():
    assert trim_router_setting({"x": "a", "y": "dead"}, {"setting": "model_group_alias", "alias": "y", "dangling": "dead"}) == {"x": "a"}

def test_trim_key_models():
    assert trim_key_field(["a", "dead"], {"field": "models", "entry": "dead"}) == ["a"]

def test_trim_key_aliases():
    assert trim_key_field({"gpt-4": "dead", "keep": "a"}, {"field": "aliases", "entry": "gpt-4"}) == {"keep": "a"}

# ── unhashable/malformed leaf values ────────────────────────────────────────
def test_router_unhashable_leaf_skipped():
    assert router_orphans([_rs("fallbacks", [{"a": [{"nested": "x"}]}])], G) == []
    assert router_orphans([_rs("fallbacks", [{"a": [["nested"]]}])], G) == []
    assert router_orphans([_rs("default_fallbacks", ["a", {"bad": 1}])], G) == []
    assert router_orphans([_rs("model_group_alias", {"x": {"bad": 1}})], G) == []

def test_key_unhashable_leaf_skipped():
    assert key_orphans([{"token": "h1", "key_alias": "ci", "models": ["a", {"bad": 1}], "aliases": {}}], G) == []
    assert key_orphans([{"token": "h1", "key_alias": "ci", "models": [], "aliases": {"n": {"bad": 1}}}], G) == []

def test_orphan_record_full_shape():
    o = router_orphans([_rs("fallbacks", [{"dead": ["a"]}])], G)[0]
    assert o["scope"] == "router" and o["location"] == "router_settings.fallbacks" and o["missing"] == ["dead"]
    k = key_orphans([{"token": "h1", "key_alias": "ci", "models": ["dead"], "aliases": {}}], G)[0]
    assert k["scope"] == "key" and k["missing"] == ["dead"]

# ── LiteLLM special model tokens ────────────────────────────────────────────
def test_key_special_model_tokens_exempt():
    for tok in ("all-proxy-models", "all-team-models", "no-default-models"):
        keys = [{"token": "h1", "key_alias": "ci", "models": ["a", tok], "aliases": {}}]
        assert key_orphans(keys, G) == [], f"{tok} should be exempt"

# ── mga_names_from and group_names folding ──────────────────────────────────
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
