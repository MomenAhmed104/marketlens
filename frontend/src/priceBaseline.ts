// Stompbox prices whose baseline is not confirmed (user decision, 20 Nov 2026). The catalogue
// price is still the one compared; this only labels it wherever it is shown next to a competitor
// price. Display only: no arithmetic, no other price is assumed.
export const UNCONFIRMED_BASELINES: Readonly<Record<string, string>> = {
  'SB-028':
    'Unconfirmed baseline: the Stompbox catalogue price £579 was recorded as a sale price ("was £599", 10 Nov 2026). ' +
    'The regular price is not confirmed, so any difference against £579 is provisional.',
}

export function baselineWarning(productId: string | null | undefined): string | null {
  return (productId && UNCONFIRMED_BASELINES[productId]) || null
}
