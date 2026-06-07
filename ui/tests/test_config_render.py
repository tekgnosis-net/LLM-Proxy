from app.config_render import effective


def test_effective_overlays_staged_flags():
    applied = [
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o"}}},
        {"kind": "router_setting", "name": "routing_strategy", "data": "simple-shuffle"},
    ]
    staged = [
        {"kind": "router_setting", "name": "routing_strategy", "data": "least-busy", "flag": "changed"},
        {"kind": "model", "name": "claude", "data": {"litellm_params": {"model": "anthropic/claude-3"}}, "flag": "new"},
        {"kind": "model", "name": "gpt", "data": {"litellm_params": {"model": "openai/gpt-4o"}}, "flag": "deleted"},
    ]
    eff = {(i["kind"], i["name"]): i for i in effective(applied, staged)}
    assert eff[("router_setting", "routing_strategy")]["data"] == "least-busy"
    assert eff[("router_setting", "routing_strategy")]["flag"] == "changed"
    assert eff[("model", "claude")]["flag"] == "new"
    assert eff[("model", "gpt")]["flag"] == "deleted"            # kept, marked
    assert eff[("model", "gpt")]["data"]["litellm_params"]["model"] == "openai/gpt-4o"
