// Per-key allowed passthrough routes: convert between picker rows (an array of
// route strings) and the wire list stored at metadata.allowed_passthrough_routes.
// LiteLLM matches these exact-or-prefix, so "/v1/audio/voices" also authorizes
// "/v1/audio/voices/combine".

export function passthroughRowsToList(rows) {
  const out = []
  for (const r of rows || []) {
    const v = (typeof r === 'string' ? r : '').trim()
    if (v && !out.includes(v)) out.push(v)   // drop blanks; dedup, first-seen order
  }
  return out
}

export function listToPassthroughRows(value) {
  if (!Array.isArray(value)) return []
  return value.filter(v => typeof v === 'string' && v.trim()).map(v => v.trim())
}
