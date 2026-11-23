// Minimal hash router: #/overview, #/products[/SB-001], #/runs, #/domains.
// The hash keeps routing separate from the ?run=<id> query parameter.
import { useSyncExternalStore } from 'react'

export type Page = 'overview' | 'products' | 'runs' | 'domains'
export const PAGES: Page[] = ['overview', 'products', 'runs', 'domains']
export const PAGE_TITLES: Record<Page, string> = {
  overview: 'Overview',
  products: 'Product Intelligence',
  runs: 'Live Runs',
  domains: 'Domain Review',
}
export const PAGE_GROUPS: Record<Page, 'Analyse' | 'Operate'> = {
  overview: 'Analyse',
  products: 'Analyse',
  runs: 'Operate',
  domains: 'Operate',
}

export interface Route {
  page: Page | null // null = no hash yet; the app picks a default
  param: string | null // e.g. product id on #/products/SB-001
}

export function parseHash(hash: string): Route {
  const [page, param] = hash.replace(/^#\/?/, '').split('/')
  return PAGES.includes(page as Page)
    ? { page: page as Page, param: param ? decodeURIComponent(param) : null }
    : { page: null, param: null }
}

/** The marketing landing page (Stage 8.2). The dashboard lives on the page routes above. */
export const HOME_HREF = '#/home'
/** Opens the dashboard on its default page (the selected run's Overview, else Live Runs). */
export const DASHBOARD_HREF = '#/dashboard'

/**
 * True when the landing page should show instead of the dashboard: an explicit #/home, or no
 * route at all and no ?run= in the URL. Every existing dashboard link (#/overview, #/products/…,
 * #/runs, #/domains, or ?run=<id> without a hash) still opens the dashboard.
 */
export function isLandingRoute(hash: string, search: string): boolean {
  const h = hash.replace(/^#\/?/, '')
  if (h === 'home') return true
  return h === '' && !new URLSearchParams(search).has('run')
}

export const href = (page: Page, param?: string | null) => `#/${page}${param ? `/${encodeURIComponent(param)}` : ''}`

export function navigate(page: Page, param?: string | null) {
  window.location.hash = href(page, param)
}

const subscribe = (cb: () => void) => {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parseHash(hash)
}
