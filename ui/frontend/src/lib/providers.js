// Catalog-driven providers. The live list comes from /api/catalog/providers
// (synced provider_endpoints_support.json). This static list is the COLD-START
// fallback only (before first catalog sync / offline) — a snapshot of LiteLLM's
// common chat providers. The slug is the litellm `provider/` prefix.
export const FALLBACK_PROVIDERS = [
  { provider: 'openai', display_name: 'OpenAI' },
  { provider: 'anthropic', display_name: 'Anthropic' },
  { provider: 'azure', display_name: 'Azure OpenAI' },
  { provider: 'gemini', display_name: 'Google Gemini' },
  { provider: 'vertex_ai', display_name: 'Google Vertex AI' },
  { provider: 'bedrock', display_name: 'AWS Bedrock' },
  { provider: 'cohere', display_name: 'Cohere' },
  { provider: 'mistral', display_name: 'Mistral' },
  { provider: 'groq', display_name: 'Groq' },
  { provider: 'deepseek', display_name: 'DeepSeek' },
  { provider: 'xai', display_name: 'xAI' },
  { provider: 'openrouter', display_name: 'OpenRouter' },
  { provider: 'together_ai', display_name: 'Together AI' },
  { provider: 'fireworks_ai', display_name: 'Fireworks AI' },
  { provider: 'perplexity', display_name: 'Perplexity' },
  { provider: 'ollama', display_name: 'Ollama (local)' },
  { provider: 'hosted_vllm', display_name: 'vLLM (hosted)' },
  { provider: 'openai_compatible', display_name: 'OpenAI-compatible / custom' },
]

// Common providers pinned to the top of the picker.
export const PINNED_PROVIDERS = ['openai', 'anthropic', 'azure', 'bedrock', 'gemini', 'vertex_ai']

// Full mode list (fallback when a provider has no catalog modes).
export const ALL_MODES = ['chat','embedding','completion','image_generation','audio_transcription','audio_speech','rerank','moderations','responses']

// Special deployment fields LiteLLM doesn't expose as data — shown only for these slugs.
export const SPECIAL_PROVIDER_FIELDS = {
  azure: ['api_base', 'api_version'],
  bedrock: ['aws_region_name'],
  vertex_ai: ['vertex_project', 'vertex_location'],
  openai_compatible: ['api_base'],   // no default URL — operator must supply the endpoint
  hosted_vllm: ['api_base'],
}

// Build litellm_params from the chosen provider slug + form. Secrets are emitted as
// os.environ/<VAR> only (config holds no literal secrets; credentials use the vault).
export function buildLitellmParams(slug, form) {
  const p = { model: `${slug}/${form.modelId}` }
  if (form.api_base) p.api_base = form.api_base
  if (form.api_version) p.api_version = form.api_version
  if (form.aws_region_name) p.aws_region_name = form.aws_region_name
  if (form.vertex_project) p.vertex_project = form.vertex_project
  if (form.vertex_location) p.vertex_location = form.vertex_location
  // api_key env-var path (only when no saved credential is selected)
  if (!form.credential && form.api_key_env) p.api_key = `os.environ/${form.api_key_env}`
  return p
}
