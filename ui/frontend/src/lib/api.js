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
  config: () => req('/api/config'),
  putConfig: (config) => req('/api/config', { method: 'PUT', body: JSON.stringify(config) }),
  keys: () => req('/api/keys'),
  createKey: (payload) => req('/api/keys', { method: 'POST', body: JSON.stringify(payload) }),
  deleteKey: (tokens) => req('/api/keys/delete', { method: 'POST', body: JSON.stringify({ tokens }) }),
  usage: () => req('/api/usage'),
  housekeeping: () => req('/api/housekeeping'),
  runHousekeeping: () => req('/api/housekeeping/run', { method: 'POST' }),
  exportConfigUrl: '/api/config/export',
  applyStatus: () => req('/api/apply/status'),
  apply: () => req('/api/apply', { method: 'POST' }),
  discard: () => req('/api/discard', { method: 'POST' }),
  cacheInfo: () => req('/api/cache/info'),
  credentials: () => req('/api/credentials'),
  createCredential: (b) => req('/api/credentials', { method: 'POST', body: JSON.stringify(b) }),
  deleteCredential: (n) => req(`/api/credentials/${encodeURIComponent(n)}`, { method: 'DELETE' }),
  testModel: (b) => req('/api/models/test', { method: 'POST', body: JSON.stringify(b) }),
  modelsHealth: () => req('/api/models/health'),
  catalogModel: (name) => req(`/api/catalog/model/${encodeURIComponent(name)}`),
  catalogProviders: () => req('/api/catalog/providers'),
  catalogStatus: () => req('/api/catalog/status'),
  catalogSync: () => req('/api/catalog/sync', { method: 'POST' }),
}
