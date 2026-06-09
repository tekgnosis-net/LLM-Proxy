// Secure-context-safe browser helpers.
//
// `crypto.randomUUID()` and `navigator.clipboard` only exist in a SECURE CONTEXT
// (https, or http://localhost / 127.0.0.1). This UI is commonly served over plain
// HTTP on a LAN IP (e.g. http://10.0.20.75:8081) — a NON-secure context — where
// those APIs are `undefined`. We fall back to APIs that are available everywhere:
// `crypto.getRandomValues` (always present) and a hidden-textarea + execCommand copy.

/** RFC-4122 v4 uuid. Uses crypto.randomUUID in secure contexts, else getRandomValues. */
export function uuidv4() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID()
  const b = crypto.getRandomValues(new Uint8Array(16))   // available in non-secure contexts
  b[6] = (b[6] & 0x0f) | 0x40   // version 4
  b[8] = (b[8] & 0x3f) | 0x80   // variant 10
  const h = [...b].map((x) => x.toString(16).padStart(2, '0'))
  return `${h[0]}${h[1]}${h[2]}${h[3]}-${h[4]}${h[5]}-${h[6]}${h[7]}-${h[8]}${h[9]}-${h[10]}${h[11]}${h[12]}${h[13]}${h[14]}${h[15]}`
}

/** Copy text to the clipboard; returns true on success. Falls back to a hidden
 *  textarea + execCommand when navigator.clipboard is unavailable (non-secure context). */
export async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (_) { /* fall through to the legacy path */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch (_) {
    return false
  }
}
