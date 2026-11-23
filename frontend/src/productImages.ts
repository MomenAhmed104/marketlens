// Product image helpers shared by the dashboard (MarketLens).
import type { ProductImage } from './api'

/** Images always load through the backend proxy, never straight from a retailer. */
export const proxied = (url: string) => `/api/images?url=${encodeURIComponent(url)}`

export function usableImage(image: ProductImage | null | undefined): image is ProductImage & { url: string } {
  return !!image && image.status === 'FOUND' && !!image.url
}

const STATUS_TEXT: Record<string, string> = {
  NOT_FOUND: 'No product image was found on the page.',
  INVALID: 'The image on the page could not be loaded.',
  BLOCKED: 'The site did not allow its page to be read for an image.',
  FETCH_FAILED: 'The page could not be read for an image.',
  NOT_ATTEMPTED: 'No image lookup was made for this listing.',
}

/** Why there is no image, in plain English (for tooltips and the viewer). */
export const imageStatusText = (image: ProductImage | null | undefined): string =>
  !image ? 'No image has been collected yet.' : (STATUS_TEXT[image.status] ?? 'No image available.')
