import { baselineWarning } from '../priceBaseline'

/** Visible label for a Stompbox price whose baseline is not confirmed (see priceBaseline.ts). */
export default function BaselineWarning({ productId }: { productId: string | null | undefined }) {
  const text = baselineWarning(productId)
  if (!text) return null
  return (
    <p className="callout warning baseline-warning" role="note" data-testid="baseline-warning">
      {text}
    </p>
  )
}
