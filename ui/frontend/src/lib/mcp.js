// MCP server form helpers: convert between UI rows and the stored item shape.
// Pure functions — node-testable without Svelte.

// The backend reconciler maintains this team (scope = all master servers);
// keys with grants join it. Must match app/mcp_reconcile.py::MCP_TEAM_ID.
export const MCP_TEAM_ID = 'ui-mcp'

export function headerRowsToDict(rows) {
  const out = {}
  for (const r of rows || []) {
    const k = (r?.k ?? '').trim()
    if (k && !(k in out)) out[k] = (r?.v ?? '').trim()
  }
  return out
}

export function dictToHeaderRows(obj) {
  return Object.entries(obj || {}).map(([k, v]) => ({ k, v: String(v ?? '') }))
}

export function costRowsToDict(rows) {
  const out = {}
  for (const r of rows || []) {
    const t = (r?.tool ?? '').trim()
    const c = Number(r?.cost)
    if (t && Number.isFinite(c) && c >= 0 && !(t in out)) out[t] = c
  }
  return out
}

export function dictToCostRows(obj) {
  return Object.entries(obj || {}).map(([tool, cost]) => ({ tool, cost: String(cost) }))
}

export function listRowsToArray(rows) {
  const out = []
  for (const r of rows || []) {
    const v = (typeof r === 'string' ? r : '').trim()
    if (v && !out.includes(v)) out.push(v)
  }
  return out
}

export function arrayToListRows(value) {
  if (!Array.isArray(value)) return []
  return value.filter(v => typeof v === 'string' && v.trim()).map(v => v.trim())
}

export function buildMcpInfo(defaultCost, costRows) {
  const ci = {}
  const dc = Number(defaultCost)
  if (defaultCost !== '' && defaultCost != null && Number.isFinite(dc) && dc >= 0) ci.default_cost_per_query = dc
  const costs = costRowsToDict(costRows)
  if (Object.keys(costs).length) ci.tool_name_to_cost_per_query = costs
  return Object.keys(ci).length ? { mcp_server_cost_info: ci } : {}
}

export function validateMcpForm(f) {
  if (!/^[A-Za-z0-9_-]+$/.test((f.server_name || '').trim())) return 'Server name is required (letters, digits, _ or - only).'
  if (!/^https?:\/\//.test((f.url || '').trim())) return 'URL is required (http:// or https://).'
  if (f.auth_type && !(f.auth_value || '').trim() && !f.hasStoredSecret) return 'Auth value is required for the selected auth type.'
  return null
}

export function mergeToolChoices(fetched, existing) {
  // Convergent merge: fetched tools become checkboxes (checked = already allowed);
  // existing entries NOT in the fetched list survive as editable rows (extras) so
  // a typed/renamed/offline tool is never silently dropped by a re-fetch.
  const names = new Set((existing || []).map(s => (typeof s === 'string' ? s : '').trim()).filter(Boolean))
  const choices = []
  const seen = new Set()
  for (const t of fetched || []) {
    const name = (t?.name || '').trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    choices.push({ name, description: t?.description || '', checked: names.has(name) })
  }
  const extras = [...names].filter(n => !seen.has(n))
  return { choices, extras }
}
