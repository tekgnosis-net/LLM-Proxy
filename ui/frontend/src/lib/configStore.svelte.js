import { api } from './api.js'

export function createConfigStore() {
  let config = $state(null)      // the full config object (source of truth in memory)
  let loading = $state(false)
  let applying = $state(false)   // true during the ~25s PUT (proxy restart)
  let error = $state('')
  let notice = $state('')

  async function load() {
    loading = true; error = ''
    try { config = await api.config() } catch (e) { error = e.message } finally { loading = false }
  }
  // Replace one top-level section then PUT the FULL config (never partial).
  async function saveSection(section, value) {
    if (!config) return
    const candidate = { ...config, [section]: value }
    applying = true; error = ''; notice = ''
    try {
      const res = await api.putConfig(candidate)
      config = candidate
      notice = `Applied — ${(res.models || []).length} model(s), routing: ${res.routing_strategy || '—'}`
      return true
    } catch (e) {
      if (e.status === 422) error = `Rejected (not applied): ${e.message}`
      else if (e.status === 409) error = `Reload failed — rolled back to the previous config: ${e.message}`
      else error = e.message
      return false
    } finally { applying = false }
  }
  return {
    get config() { return config }, get loading() { return loading },
    get applying() { return applying }, get error() { return error }, get notice() { return notice },
    load, saveSection,
  }
}
