export const money = (n) => `$${Number(n ?? 0).toFixed(4)}`
export function fmtMs(ms) {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
