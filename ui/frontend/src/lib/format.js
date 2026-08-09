export const money = (n) => `$${Number(n ?? 0).toFixed(4)}`
export function fmtMs(ms) {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
// Compact local date+time; year appears only when it differs from `now`'s
// (History ranges up to 90d can cross a year boundary).
export function fmtDateTime(t, now = new Date()) {
  const d = new Date(t)
  if (isNaN(d)) return '—'
  const opts = { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
  return d.toLocaleString(undefined, opts)
}
