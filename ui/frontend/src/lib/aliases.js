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
