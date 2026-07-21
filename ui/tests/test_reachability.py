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
    assert SEMANTICS_VERSION == "1.89.2" and "groq" in PROVIDER_LIST and len(PROVIDER_LIST) == 141

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

def test_over_reach_resolves_mga_name():
    cols = [_collision("real-group", ["t"])]
    # key allows alias name 'myalias' -> resolves to 'real-group' via mga
    keys = [{"token": "h1", "key_alias": "k", "models": ["myalias"], "aliases": {}}]
    o = key_over_reach(keys, cols, groups={"real-group", "t", "myalias"}, mga={"myalias": "real-group"})
    assert len(o) == 1 and o[0]["extra"][0]["target"] == "t"

def test_over_reach_resolves_per_key_alias():
    # key allows alias NAME 'myalias' resolved via the key's OWN aliases (not mga)
    cols = [_collision("real-group", ["t"])]
    keys = [{"token": "h1", "key_alias": "k", "models": ["myalias"], "aliases": {"myalias": "real-group"}}]
    o = key_over_reach(keys, cols, groups={"real-group", "t", "myalias"}, mga={})
    assert len(o) == 1 and o[0]["extra"][0]["target"] == "t"
