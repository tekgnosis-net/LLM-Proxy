from app.catalog import parse_pricing, parse_endpoints, endpoints_to_modes


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


def test_endpoints_to_modes_maps_supported_only():
    eps = {"chat_completions": True, "embeddings": True, "rerank": False, "image_generation": True}
    modes = endpoints_to_modes(eps)
    assert "chat" in modes and "embedding" in modes and "image_generation" in modes
    assert "rerank" not in modes


def test_endpoints_to_modes_accepts_json_string():
    import json
    assert "chat" in endpoints_to_modes(json.dumps({"chat_completions": True}))


def test_endpoints_to_modes_empty():
    assert endpoints_to_modes(None) == [] and endpoints_to_modes({}) == []


def test_endpoints_to_modes_real_matrix_plural_keys():
    """Regression: litellm's provider_endpoints_support.json uses PLURAL keys
    (audio_transcriptions, image_generations) and text_completion — the mapper
    silently dropped these three modes for EVERY provider (found via the openai
    row on the live host)."""
    openai_row = {"chat_completions": True, "text_completion": True, "embeddings": True,
                  "moderations": True, "audio_speech": True, "audio_transcriptions": True,
                  "image_generations": True, "responses": True, "rerank": False}
    modes = endpoints_to_modes(openai_row)
    assert "audio_transcription" in modes
    assert "image_generation" in modes
    assert "completion" in modes
    assert "audio_speech" in modes and "chat" in modes and "rerank" not in modes


def test_endpoints_to_modes_singular_keys_still_map():
    # older/singular spellings keep working (both schema generations accepted)
    assert endpoints_to_modes({"audio_transcription": True}) == ["audio_transcription"]
    assert endpoints_to_modes({"image_generation": True}) == ["image_generation"]
    assert endpoints_to_modes({"completion": True}) == ["completion"]
