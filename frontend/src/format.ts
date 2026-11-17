// Display helpers. Formatting only: no price arithmetic happens in the browser.

/** Format a backend decimal string. null stays "missing"; "0.00" is shown as £0.00, never hidden. */
export function money(amount: string | null, currency: string | null): string {
  if (amount === null) return 'missing'
  const n = Number(amount)
  if (!Number.isFinite(n)) return amount
  if (!currency) return amount
  try {
    return new Intl.NumberFormat('en-GB', { style: 'currency', currency }).format(n)
  } catch {
    return `${amount} ${currency}`
  }
}

/** Signed GBP difference (competitor − Stompbox), as computed by the backend. */
export function signedGbp(diff: string): string {
  const n = Number(diff)
  if (!Number.isFinite(n)) return diff
  const abs = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(Math.abs(n))
  return n > 0 ? `+${abs}` : n < 0 ? `−${abs}` : abs
}

export function diffDirection(diff: string): string {
  const n = Number(diff)
  return n > 0 ? 'competitor higher' : n < 0 ? 'competitor lower' : 'same price'
}

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** "13 Nov 2026, 17:02" (no seconds). */
export const when = (iso: string | null) => (iso ? DATE_TIME.format(new Date(iso)) : 'unknown')

/** "1 candidate", "3 candidates"; irregular plurals passed explicitly. */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

export const timeOnly = (iso: string) => new Date(iso).toLocaleTimeString('en-GB')

/** "SIMILAR_MATCH" -> "Similar match", "product_search" -> "Product search". */
export function label(value: string | null | undefined): string {
  if (!value) return 'unknown'
  const s = value.replace(/_/g, ' ').toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export const usd = (v: string | null) => (v === null ? 'not reported' : `$${Number(v).toFixed(3)}`)
