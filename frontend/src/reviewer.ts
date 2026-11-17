import { useCallback, useSyncExternalStore } from 'react'

const REVIEWER_KEY = 'stompbox.reviewer'
const listeners = new Set<() => void>()

const read = () => localStorage.getItem(REVIEWER_KEY) ?? ''

function subscribe(onChange: () => void) {
  listeners.add(onChange)
  window.addEventListener('storage', onChange) // other browser tabs
  return () => {
    listeners.delete(onChange)
    window.removeEventListener('storage', onChange)
  }
}

/**
 * Reviewer name, remembered in localStorage. One shared value: the dashboard and the
 * review tab are mounted together, so typing it in one updates the other.
 */
export function useReviewer() {
  const reviewer = useSyncExternalStore(subscribe, read)
  const setReviewer = useCallback((name: string) => {
    localStorage.setItem(REVIEWER_KEY, name)
    listeners.forEach((l) => l())
  }, [])
  return [reviewer, setReviewer] as const
}
