import { api } from './api.js'

export function createConfigStore() {
  let config = $state(null), loading = $state(false), saving = $state(false)
  let applying = $state(false), error = $state(''), notice = $state('')
  let pending = $state(false), pendingSummary = $state([])

  async function refreshPending() {
    try { const s = await api.applyStatus(); pending = s.pending; pendingSummary = s.summary || [] } catch {}
  }
  async function load() {
    loading = true; error = ''
    try { config = await api.config(); await refreshPending() } catch (e) { error = e.message } finally { loading = false }
  }
  async function saveSection(section, value) {
    if (!config) return false
    const candidate = { ...config, [section]: value }
    saving = true; error = ''; notice = ''
    try {
      const res = await api.putConfig(candidate)
      config = candidate; pending = res.pending; pendingSummary = res.summary || []
      notice = 'Saved. Click Apply to restart the proxy and make it live.'
      return true
    } catch (e) { error = e.status === 422 ? `Rejected: ${e.message}` : e.message; return false }
    finally { saving = false }
  }
  async function apply() {
    applying = true; error = ''; notice = ''
    try {
      const res = await api.apply()
      notice = `Applied — ${(res.models||[]).length} model(s), routing ${res.routing_strategy||'—'}`
      await refreshPending(); return true
    } catch (e) {
      error = e.status === 409 ? `Reload failed — rolled back: ${e.message}` : e.message
      await refreshPending(); return false
    } finally { applying = false }
  }
  async function discard() {
    saving = true; error = ''; notice = ''
    try {
      await api.discard()
      config = await api.config()
      await refreshPending()
      notice = 'Discarded unapplied changes — reverted to the last applied config.'
      return true
    } catch (e) { error = e.message; await refreshPending(); return false }
    finally { saving = false }
  }
  return {
    get config(){return config}, get loading(){return loading}, get saving(){return saving},
    get applying(){return applying}, get error(){return error}, get notice(){return notice},
    get pending(){return pending}, get pendingSummary(){return pendingSummary},
    load, saveSection, apply, discard, refreshPending,
  }
}
