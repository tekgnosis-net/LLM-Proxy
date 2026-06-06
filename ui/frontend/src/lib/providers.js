// Provider presets: how to build litellm_params.model + which fields/secret env var.
export const PROVIDERS = [
  { id: 'openai',     label: 'OpenAI',            prefix: 'openai/',     keyEnv: 'OPENAI_API_KEY',    fields: ['api_key'] },
  { id: 'anthropic',  label: 'Anthropic',         prefix: 'anthropic/',  keyEnv: 'ANTHROPIC_API_KEY', fields: ['api_key'] },
  { id: 'azure',      label: 'Azure OpenAI',      prefix: 'azure/',      keyEnv: 'AZURE_API_KEY',     fields: ['api_key', 'api_base', 'api_version'] },
  { id: 'gemini',     label: 'Google Gemini',     prefix: 'gemini/',     keyEnv: 'GEMINI_API_KEY',    fields: ['api_key'] },
  { id: 'bedrock',    label: 'AWS Bedrock',       prefix: 'bedrock/',    keyEnv: null,                fields: ['aws_region_name'] },
  { id: 'openai_compat', label: 'OpenAI-compatible / local (vLLM, Ollama)', prefix: 'openai/', keyEnv: null, fields: ['api_base', 'api_key'], customProvider: 'openai' },
]
// Secrets are emitted as os.environ/<VAR>, never literals (config.yaml has no secrets).
export function buildLitellmParams(provider, form) {
  const p = { model: provider.prefix + form.modelId }
  if (provider.customProvider) p.custom_llm_provider = provider.customProvider
  if (form.api_base) p.api_base = form.api_base
  if (form.api_version) p.api_version = form.api_version
  if (form.aws_region_name) p.aws_region_name = form.aws_region_name
  // api_key: store as an env reference the operator sets in .env (never the literal)
  if (provider.fields.includes('api_key') && form.api_key_env) p.api_key = `os.environ/${form.api_key_env}`
  return p
}
