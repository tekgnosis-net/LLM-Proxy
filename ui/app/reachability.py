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
