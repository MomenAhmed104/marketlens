import { useEffect, useRef, type ReactNode } from 'react'
import { CloseIcon } from './icons'

interface Props {
  title: ReactNode
  subtitle?: ReactNode
  onClose: () => void
  children: ReactNode
}

const FOCUSABLE = 'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'

/**
 * Modal side panel (full-screen sheet on small screens). Focus moves into it,
 * Tab is trapped, Escape closes it, and focus returns to the opener.
 */
export default function Drawer({ title, subtitle, onClose, children }: Props) {
  const panel = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  useEffect(() => {
    closeRef.current = onClose
  })

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    panel.current?.querySelector<HTMLElement>('.drawer-close')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeRef.current()
        return
      }
      if (e.key !== 'Tab' || !panel.current) return
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus()
    }
  }, [])

  return (
    <div className="drawer-layer">
      <div className="drawer-backdrop" aria-hidden="true" onClick={() => closeRef.current()} />
      <div ref={panel} className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className="drawer-head">
          <div>
            <h2 id="drawer-title">{title}</h2>
            {subtitle && <p className="drawer-sub">{subtitle}</p>}
          </div>
          <button type="button" className="icon-btn drawer-close" aria-label="Close" onClick={() => closeRef.current()}>
            <CloseIcon />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </div>
    </div>
  )
}
