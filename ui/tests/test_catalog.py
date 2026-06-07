from app.catalog import parse_pricing, parse_endpoints


def test_parse_pricing_skips_sample_spec_and_extracts_fields():
    data = {
        "sample_spec": {"input_cost_per_token": 0, "note": "doc only"},
        "gpt-4o": {"litellm_provider": "openai", "mode": "chat",
                   "input_cost_per_token": 2.5e-06, "output_cost_per_token": 1e-05,
                   "max_input_tokens": 128000, "max_output_tokens": 16384,
                   "supports_vision": True, "supports_function_calling": True},
    }
    rows = parse_pricing(data)
    assert "sample_spec" not in [r["model_name"] for r in rows]
    row = next(r for r in rows if r["model_name"] == "gpt-4o")
    assert row["input_cost_per_token"] == 2.5e-06 and row["mode"] == "chat" and row["litellm_provider"] == "openai"
    assert row["max_input_tokens"] == 128000
    assert row["supports"]["supports_vision"] is True


def test_parse_pricing_tolerates_sparse_entries():
    rows = parse_pricing({"text-embedding-3-small": {"litellm_provider": "openai", "mode": "embedding",
                                                     "input_cost_per_token": 2e-08}})
    r = rows[0]
    assert r["output_cost_per_token"] is None and r["max_input_tokens"] is None and r["mode"] == "embedding"


def test_parse_endpoints_extracts_provider_matrix():
    data = {"_comment": "x", "_schema": {}, "endpoints": {},
            "providers": {"anthropic": {"display_name": "Anthropic", "url": "https://docs…",
                                        "endpoints": {"chat_completions": True, "embeddings": False}}}}
    rows = parse_endpoints(data)
    r = next(x for x in rows if x["provider"] == "anthropic")
    assert r["display_name"] == "Anthropic" and r["endpoints"]["chat_completions"] is True
    assert all(x["provider"] not in ("_comment", "_schema", "endpoints") for x in rows)
