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
}
