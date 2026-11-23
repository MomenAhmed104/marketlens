import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { revealInRow } from './revealInRow'
import ScrollRow from './ScrollRow'

export interface TabItem<T extends string> {
  id: T
  label: ReactNode
  count?: number | null
}

interface Props<T extends string> {
  label: string // accessible name of the tablist
  idPrefix: string
  tabs: TabItem<T>[]
  value: T
  onChange: (id: T) => void
  variant?: 'line' | 'pill'
}

const tabId = (prefix: string, id: string) => `${prefix}-tab-${id}`
const panelId = (prefix: string, id: string) => `${prefix}-panel-${id}`

/** WAI-ARIA tabs: roving tabindex, arrow keys, Home and End. Pair with <TabPanel>. */
export default function Tabs<T extends string>({ label, idPrefix, tabs, value, onChange, variant = 'line' }: Props<T>) {
  const refs = useRef<Partial<Record<string, HTMLButtonElement | null>>>({})
  const first = useRef(true)
  // Keep the selected tab visible when the row scrolls (not on first render: no page jump).
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    revealInRow(refs.current[value])
  }, [value])
  const onKeyDown = (e: KeyboardEvent, i: number) => {
    const last = tabs.length - 1
    let next: number | null = null
    if (e.key === 'ArrowRight') next = i === last ? 0 : i + 1
    else if (e.key === 'ArrowLeft') next = i === 0 ? last : i - 1
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = last
    if (next === null) return
    e.preventDefault()
    onChange(tabs[next].id)
    refs.current[tabs[next].id]?.focus()
  }
  return (
    <ScrollRow trackClassName={`tabs-${variant}`} role="tablist" aria-label={label}>
      {tabs.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[t.id] = el
          }}
          id={tabId(idPrefix, t.id)}
          type="button"
          role="tab"
          aria-selected={value === t.id}
          aria-controls={panelId(idPrefix, t.id)}
          tabIndex={value === t.id ? 0 : -1}
          className="tab-btn"
          onClick={() => onChange(t.id)}
          onKeyDown={(e) => onKeyDown(e, i)}
        >
          {t.label}
          {t.count !== undefined && t.count !== null && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </ScrollRow>
  )
}

export function TabPanel({ idPrefix, id, children }: { idPrefix: string; id: string; children: ReactNode }) {
  return (
    <div id={panelId(idPrefix, id)} role="tabpanel" aria-labelledby={tabId(idPrefix, id)} className="tab-panel">
      {children}
    </div>
  )
}
