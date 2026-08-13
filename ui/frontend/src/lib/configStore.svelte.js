import { api } from './api.js'

export function createConfigStore() {
  let items = $state([])        // [{kind,name,data,flag}]
  let loading = $state(false), saving = $state(false), applying = $state(false)
  let error = $state(''), notice = $state('')
  let pending = $state(false), count = $state(0)
  let storeModelInDb = $state(false)

  async function load() {
    loading = true; error = ''
    try { const s = await api.configState(); items = s.items || []; pending = s.pending; count = s.count; storeModelInDb = !!s.store_model_in_db }
    catch (e) { error = e.message } finally { loading = false }
  }
  function itemsOfKind(kind) { return items.filter(i => i.kind === kind) }
  function itemNamed(kind, name) { return items.find(i => i.kind === kind && i.name === name) }

  async function stageItem(kind, name, data) {
    saving = true; error = ''; notice = ''
    try { const r = await api.stageItem(kind, name, data); pending = r.pending; count = r.count; await load()
      notice = 'Staged. Click Apply to make it live.'; return true }
    catch (e) { error = e.status === 422 ? `Rejected: ${e.message}` : e.message; return false }
    finally { saving = false }
  }
  async function deleteItem(kind, name) {
    saving = true; error = ''; notice = ''
    try { const r = await api.deleteItem(kind, name); pending = r.pending; count = r.count; await load(); return true }
    catch (e) { error = e.message; return false } finally { saving = false }
  }
  async function apply() {
    applying = true; error = ''; notice = ''
    try {
      const r = await api.apply()
      if (r.hybrid) {
        const m = r.models || {}
        const mc = r.mcp
        let msg = `Applied live — ${m.added || 0} added, ${m.updated || 0} updated, ${m.deleted || 0} deleted`
        if (mc) msg += `; MCP — ${mc.added || 0} added, ${mc.updated || 0} updated, ${mc.deleted || 0} deleted`
        if (r.restart === 'healthy') msg += '; settings change restarted the proxy (healthy)'
        else if (r.restart === 'unhealthy') msg += `; settings restart UNHEALTHY: ${r.detail || ''}`
        const allFailed = [...(m.failed || []).map(f => `${f.id} (${f.op})`),
                           ...((mc && mc.failed) || []).map(f => `MCP ${f.id} (${f.op})`)]
        if (allFailed.length) {
          error = `${msg}. ${allFailed.length} op(s) failed: ${allFailed.join(', ')}`
          notice = ''
        } else {
          notice = msg
        }
      } else {
        notice = r.servant === 'healthy'
          ? 'Applied — proxy restarted and healthy.'
          : `Applied, but the proxy is unhealthy: ${r.detail || ''} — fix the setting and re-Apply.`
      }
      await load(); return true
    } catch (e) { error = e.status === 422 ? `Invalid config: ${e.message}` : e.message; await load(); return false }
    finally { applying = false }
  }
  async function discard(kind, name) {
    saving = true; error = ''; notice = ''
    try {
      const q = kind && name ? `?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}` : ''
      await api.discard(q); await load(); notice = 'Discarded staged changes.'; return true
    } catch (e) { error = e.message; await load(); return false } finally { saving = false }
  }
  return {
    get items(){return items}, get loading(){return loading}, get saving(){return saving},
    get applying(){return applying}, get error(){return error}, get notice(){return notice},
    get pending(){return pending}, get count(){return count},
    get storeModelInDb(){return storeModelInDb},
    load, itemsOfKind, itemNamed, stageItem, deleteItem, apply, discard,
  }
}
