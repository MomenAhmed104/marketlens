// A single horizontal row that scrolls instead of wrapping or clipping (Stage 8.2). When more
// content is hidden on either side, a fade and a chevron show it, so narrow screens never hide
// items silently and the page itself never scrolls sideways.
import { useCallback, useEffect, useRef, useState, type HTMLAttributes, type ReactNode } from 'react'

interface Props extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  /** Classes for the scrolling element itself (e.g. the tab list). */
  trackClassName?: string
}

export default function ScrollRow({ children, trackClassName = '', className = '', ...trackProps }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })

  const update = useCallback(() => {
    const el = ref.current
    if (!el) return
    const left = el.scrollLeft > 1
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1
    setEdges((e) => (e.left === left && e.right === right ? e : { left, right }))
  }, [])

  useEffect(() => {
    update()
    const el = ref.current
    window.addEventListener('resize', update)
    const ro = typeof ResizeObserver !== 'undefined' && el ? new ResizeObserver(update) : null
    if (ro && el) ro.observe(el)
    return () => {
      window.removeEventListener('resize', update)
      ro?.disconnect()
    }
  }, [update])

  const cls = ['scroll-row', className, edges.left && 'more-left', edges.right && 'more-right'].filter(Boolean).join(' ')
  return (
    <div className={cls} data-more-left={edges.left || undefined} data-more-right={edges.right || undefined}>
      <div ref={ref} className={`scroll-row-track ${trackClassName}`.trim()} onScroll={update} {...trackProps}>
        {children}
      </div>
      <span className="scroll-hint scroll-hint-left" aria-hidden="true">‹</span>
      <span className="scroll-hint scroll-hint-right" aria-hidden="true">›</span>
    </div>
  )
}
