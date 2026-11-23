/** Brings an element into view inside its scroll row without moving the page vertically. */
export function revealInRow(el: Element | null | undefined) {
  if (el && typeof (el as HTMLElement).scrollIntoView === 'function') {
    ;(el as HTMLElement).scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }
}
