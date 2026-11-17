// Run switcher: a button that opens a keyboard-accessible list of the runs still
// in backend memory (listbox pattern: arrows, Home/End, Enter, Escape).
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { listRuns, type ExecutionRun } from '../api'
import StatusPill from '../components/StatusPill'
import { ChevronDownIcon } from '../components/icons'
import { when } from '../format'
import { useRunContext } from './runContext'

export default function RunSwitcher() {
  const { runId, openRun, view, catalogue } = useRunContext()
  const [runs, setRuns] = useState<ExecutionRun[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const button = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const id = useId()
  const status = view.run?.status

  useEffect(() => {
    listRuns().then(setRuns, () => setRuns([]))
  }, [runId, status])

  const productText = (r: ExecutionRun) => {
    const first = r.request.product_ids[0]
    const name = catalogue.get(first)?.product_name ?? first
    const more = r.request.product_ids.length - 1
    return more > 0 ? `${name} +${more} more` : name
  }

  // Options: every run in memory, then "no run".
  const options: (ExecutionRun | null)[] = [...runs, null]
  const current = runs.find((r) => r.run_id === runId) ?? view.run

  const show = () => {
    listRuns().then(setRuns, () => {})
    const i = runs.findIndex((r) => r.run_id === runId)
    setActive(i >= 0 ? i : 0)
    setOpen(true)
  }
  useEffect(() => {
    if (open) list.current?.focus()
  }, [open])

  const close = () => {
    setOpen(false)
    button.current?.focus()
  }
  const choose = (r: ExecutionRun | null) => {
    openRun(r ? r.run_id : null)
    close()
  }
  const onListKey = (e: KeyboardEvent) => {
    const last = options.length - 1
    if (e.key === 'ArrowDown') setActive((a) => Math.min(a + 1, last))
    else if (e.key === 'ArrowUp') setActive((a) => Math.max(a - 1, 0))
    else if (e.key === 'Home') setActive(0)
    else if (e.key === 'End') setActive(last)
    else if (e.key === 'Enter' || e.key === ' ') choose(options[active])
    else if (e.key === 'Escape' || e.key === 'Tab') {
      if (e.key === 'Escape') close()
      else setOpen(false)
      return
    } else return
    e.preventDefault()
  }

  return (
    <div className="switcher">
      <button
        ref={button}
        type="button"
        className="switcher-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-label={`Selected run: ${current ? `${productText(current)}, ${current.status.toLowerCase().replace(/_/g, ' ')}` : 'none'}`}
        onClick={() => (open ? close() : show())}
      >
        <span className="switcher-eyebrow">Run</span>
        <span className="switcher-main">{current ? productText(current) : 'No run selected'}</span>
        {current && <StatusPill value={current.status} />}
        {current && <span className="switcher-date">{when(current.created_at)}</span>}
        {!current && runId && view.connection === 'expired' && <span className="switcher-date">expired</span>}
        <ChevronDownIcon />
      </button>
      {open && (
        <>
          <div className="switcher-scrim" aria-hidden="true" onClick={() => setOpen(false)} />
          <ul
            ref={list}
            id={`${id}-list`}
            className="switcher-list"
            role="listbox"
            aria-label="Runs in memory"
            tabIndex={-1}
            aria-activedescendant={`${id}-opt-${active}`}
            onKeyDown={onListKey}
          >
            {options.map((r, i) => (
              <li
                key={r?.run_id ?? 'none'}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={r ? r.run_id === runId : runId === null}
                className={i === active ? 'switcher-option active' : 'switcher-option'}
                onClick={() => choose(r)}
                onMouseEnter={() => setActive(i)}
              >
                {r ? (
                  <>
                    <span className="opt-main">{productText(r)}</span>
                    <StatusPill value={r.status} />
                    <span className="opt-meta">
                      {r.request.product_ids.join(', ')} · {when(r.created_at)}
                    </span>
                  </>
                ) : (
                  <span className="opt-main opt-none">No run selected</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
