export function qs(params) {
  const u = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') u.append(k, v)
  })
  return u.toString()
}

// Requests started while a page is loading carry that page's abort signal, so leaving the page or changing
// a filter cancels them (and the server then stops their database queries).
let currentSignal = null
export function withSignal(signal, fn) {
  const prev = currentSignal
  currentSignal = signal
  try { return fn() } finally { currentSignal = prev }
}

export async function get(path, params = {}) {
  const opts = currentSignal ? { signal: currentSignal } : undefined
  let res = await fetch(`/api${path}?${qs(params)}`, opts)
  // a reporting view could not be read: the server has switched to live silver data - ask once more
  if (res.status === 503 && res.headers.get('X-Retry-Silver')) res = await fetch(`/api${path}?${qs(params)}`, opts)
  if (!res.ok) {
    let msg = res.statusText
    try { msg = (await res.json()).detail || msg } catch {}
    throw new Error(msg)
  }
  return res.json()
}

export async function post(path, body) {
  const res = await fetch(`/api${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) {
    let msg = res.statusText
    try { msg = (await res.json()).detail || msg } catch {}
    throw new Error(msg)
  }
  return res.json()
}

const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })
const inr0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
export const num = (v, d = 2) =>
  v === null || v === undefined || v === '' ? '—'
    : new Intl.NumberFormat('en-IN', { maximumFractionDigits: d }).format(v)
export const money = (v) => (v === null || v === undefined ? '—' : '₹ ' + inr.format(v))
export const money0 = (v) => (v === null || v === undefined ? '—' : '₹ ' + inr0.format(v))
export const compact = (v) => {
  if (v === null || v === undefined) return '—'
  const a = Math.abs(v)
  if (a >= 1e7) return '₹ ' + (v / 1e7).toFixed(2) + ' Cr'
  if (a >= 1e5) return '₹ ' + (v / 1e5).toFixed(2) + ' L'
  return '₹ ' + inr0.format(v)
}

// categorical palette (fixed order, never cycled) - validated reference palette
export const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
export const OTHER = '#9a9893'
