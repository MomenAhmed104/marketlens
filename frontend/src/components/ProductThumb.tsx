// Product images (MarketLens). One component for every image in the dashboard, so sizes, aspect
// ratio (square, object-fit: contain) and the placeholder are consistent everywhere.
//
// Images always load through the backend proxy (/api/images), never straight from a retailer.
// An image is display-only: it opens a larger view on click, never the product page, and it is
// never evidence of a match. A missing, failed or unusable image shows the same placeholder.
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ProductImage } from '../api'
import { imageStatusText, proxied, usableImage } from '../productImages'
import { ExternalIcon } from './icons'

export type ThumbSize = 'sm' | 'md' | 'lg'

function Placeholder({ size, title }: { size: ThumbSize; title: string }) {
  return (
    <span className={`thumb thumb-${size} thumb-empty`} role="img" aria-label={`No image: ${title}`} title={title}>
      <svg viewBox="0 0 24 24" width="40%" height="40%" fill="none" stroke="currentColor" strokeWidth={1.6}
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="9" cy="9" r="2" />
        <path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21" />
      </svg>
    </span>
  )
}

interface Props {
  image: ProductImage | null | undefined
  /** What the image shows, e.g. the product or listing title. */
  alt: string
  size?: ThumbSize
  /** Click opens a larger view (default true). */
  zoomable?: boolean
}

export default function ProductThumb({ image, alt, size = 'sm', zoomable = true }: Props) {
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(false)
  if (!usableImage(image) || failed) {
    return <Placeholder size={size} title={failed ? 'The image could not be loaded.' : imageStatusText(image)} />
  }
  const img = <img src={proxied(image.url)} alt={alt} loading="lazy" decoding="async" onError={() => setFailed(true)} />
  if (!zoomable) return <span className={`thumb thumb-${size}`}>{img}</span>
  return (
    <>
      <button type="button" className={`thumb thumb-${size} thumb-button`} aria-label={`Enlarge image: ${alt}`}
        aria-haspopup="dialog" onClick={() => setOpen(true)}>
        {img}
      </button>
      {open && <ImageViewer image={image} alt={alt} onClose={() => setOpen(false)} />}
    </>
  )
}

/** A larger view with the image's source. Escape or the close button returns focus to the thumbnail. */
function ImageViewer({ image, alt, onClose }: { image: ProductImage & { url: string }; alt: string; onClose: () => void }) {
  const closeBtn = useRef<HTMLButtonElement>(null)
  const closeRef = useRef(onClose)
  useEffect(() => {
    closeRef.current = onClose
  })
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeBtn.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation() // capture phase: an evidence drawer underneath stays open
        closeRef.current()
      } else if (e.key === 'Tab') {
        // Keep focus inside the viewer, cycling through its links and button.
        const items = [...(closeBtn.current?.closest('[role="dialog"]')?.querySelectorAll<HTMLElement>('a[href], button') ?? [])]
        if (items.length === 0) return
        e.preventDefault()
        e.stopPropagation()
        const i = items.indexOf(document.activeElement as HTMLElement)
        items[(i + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      opener?.focus()
    }
  }, [])
  return createPortal(
    <div className="viewer-backdrop" onClick={onClose}>
      <div className="viewer" role="dialog" aria-modal="true" aria-label={`Image: ${alt}`} onClick={(e) => e.stopPropagation()}>
        <div className="viewer-head">
          <span className="viewer-title">{alt}</span>
          <button ref={closeBtn} type="button" className="btn small-btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="viewer-img">
          <img src={proxied(image.url)} alt={alt} />
        </div>
        <p className="viewer-meta">
          {image.source_page_url && (
            <a href={image.source_page_url} target="_blank" rel="noreferrer noopener">
              Product page <ExternalIcon />
            </a>
          )}
          <a href={image.url} target="_blank" rel="noreferrer noopener">
            Image source <ExternalIcon />
          </a>
          <span className="hint-inline">Shown for reference only; images never decide a match.</span>
        </p>
      </div>
    </div>,
    document.body,
  )
}
