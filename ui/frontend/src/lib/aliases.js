// Per-key model aliases: convert between picker rows and LiteLLM's wire map.
// Wire shape: { "<alias>": "<target-model>", ... }.  A picker row is
// { name: string, target: string }.  The alias name is free text; the target
// is a real model the key is allowed to call.

export function rulesToAliases(rows) {
  const out = {}
  for (const r of rows || []) {
    const name = (r.name || '').trim()
    const target = (r.target || '').trim()
    if (name && target) out[name] = target   // dup names: last wins (dict semantics)
  }
  return out
}

export function aliasesToRules(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value)
    .filter(([k, v]) => typeof k === 'string' && typeof v === 'string')
    .map(([name, target]) => ({ name, target }))
}

// LiteLLM checks the RAW requested model name against a key's allowed models
// BEFORE resolving a per-key alias (issue #25281), so on a RESTRICTED key the
// alias name must itself be in allowed models or the request 403s. An
// unrestricted key (empty models = all allowed) needs nothing.
export function withAliasNames(models, aliasNames) {
  if (!models || models.length === 0) return models || []   // unrestricted → leave as-is
  return [...new Set([...models, ...(aliasNames || [])])]
}

// Inverse for display: hide the injected alias names from the Allowed-models UI,
// so the user manages real models and aliases separately.
export function stripAliasNames(models, aliases) {
  const names = new Set(Object.keys(aliases || {}))
  return (models || []).filter(m => !names.has(m))
}
