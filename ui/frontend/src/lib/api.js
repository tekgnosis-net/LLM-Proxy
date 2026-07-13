async function req(path, opts = {}) {
  const r = await fetch(path, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...opts })
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail || r.statusText
    const err = new Error(detail); err.status = r.status; throw err
  }
  return r.json()
}
export const api = {
  me: () => req('/api/auth/me'),
  login: (password) => req('/api/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => req('/api/auth/logout', { method: 'POST' }),
  health: () => req('/api/health'),
  configState: () => req('/api/config/state'),
  stageItem: (kind, name, data) => req('/api/config/item', { method: 'PUT', body: JSON.stringify({ kind, name, data }) }),
  deleteItem: (kind, name) => req(`/api/config/item/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  configRendered: () => req('/api/config/rendered'),
  passthroughGet: () => req('/api/config/passthrough'),
  passthroughPut: (yaml) => req('/api/config/passthrough', { method: 'PUT', body: JSON.stringify({ yaml }) }),
  keys: () => req('/api/keys'),
  createKey: (payload) => req('/api/keys', { method: 'POST', body: JSON.stringify(payload) }),
  deleteKey: (tokens) => req('/api/keys/delete', { method: 'POST', body: JSON.stringify({ tokens }) }),
  usage: () => req('/api/usage'),
  housekeeping: () => req('/api/housekeeping'),
  runHousekeeping: () => req('/api/housekeeping/run', { method: 'POST' }),
  exportConfigUrl: '/api/config/export',
  apply: () => req('/api/apply', { method: 'POST' }),
  discard: (q = '') => req('/api/discard' + q, { method: 'POST' }),
  testModel: (b) => req('/api/models/test', { method: 'POST', body: JSON.stringify(b) }),
  modelsHealth: () => req('/api/models/health'),
  catalogModel: (name) => req(`/api/catalog/model/${encodeURIComponent(name)}`),
  catalogProviders: () => req('/api/catalog/providers'),
  catalogStatus: () => req('/api/catalog/status'),
  catalogSync: () => req('/api/catalog/sync', { method: 'POST' }),
  cacheStats: () => req('/api/cache/stats'),
  proxyInfo: () => req('/api/proxy-info'),
  get: (path) => req(path),
  post: (path, body) => req(path, { method: 'POST', body: JSON.stringify(body) }),
  prepareHotApply: () => req('/api/config/prepare-hot-apply', { method: 'POST' }),
  drift: () => req('/api/config/drift'),
  resync: () => req('/api/config/resync', { method: 'POST' }),
  integrity: () => req('/api/config/integrity'),
  integrityFix: (orphan, dry_run) => req('/api/config/integrity/fix', { method: 'POST', body: JSON.stringify({ orphan, dry_run }) }),
}
