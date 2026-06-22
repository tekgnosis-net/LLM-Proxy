// Per-key router fallbacks: convert between the structured picker model and
// LiteLLM's wire format.  LiteLLM shape: [{ "<primary>": ["<backup>", ...] }, ...]
//
// A picker "rule" is { primary: string, backups: string[] }.  Both ends are
// public model names; the picker sources them from the key's Allowed models so
// an unreachable fallback can't be expressed.

export function rulesToFallbacks(rules) {
  const out = []
  for (const r of rules || []) {
    const primary = (r.primary || '').trim()
    // never let a model fall back to itself, and drop blanks
    const backups = (r.backups || []).filter(b => b && b !== primary)
    if (primary && backups.length) out.push({ [primary]: backups })
  }
  return out
}

// Parse a LiteLLM fallbacks value (already-parsed array, or null) into picker
// rules.  Returns { rules, representable }.  representable=false means the value
// can't be shown in the picker (parse junk, multi-key objects, the "*" wildcard,
// or non-string backups) → caller should fall back to the raw-JSON editor.
export function fallbacksToRules(value) {
  if (value == null || (Array.isArray(value) && value.length === 0))
    return { rules: [], representable: true }
  if (!Array.isArray(value)) return { rules: [], representable: false }
  const rules = []
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item))
      return { rules: [], representable: false }
    const keys = Object.keys(item)
    if (keys.length !== 1) return { rules: [], representable: false }
    const primary = keys[0]
    if (primary === '*') return { rules: [], representable: false }   // wildcard → JSON mode
    const backups = item[primary]
    if (!Array.isArray(backups) || !backups.every(b => typeof b === 'string'))
      return { rules: [], representable: false }
    rules.push({ primary, backups: [...backups] })
  }
  return { rules, representable: true }
}
