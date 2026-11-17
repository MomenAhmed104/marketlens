import { statusMeta, type StatusKind } from '../status'

interface Props {
  value: string | null | undefined
  kind?: StatusKind
  text?: string // overrides the label; the tone and icon still come from `value`
  title?: string
}

/** Status shown as tone + icon + label, never colour alone. */
export default function StatusPill({ value, kind = 'default', text, title }: Props) {
  const m = statusMeta(value, kind)
  const running = value === 'RUNNING'
  return (
    <span className={`pill tone-${m.tone}`} title={title}>
      <span className={running ? 'pill-icon spin' : 'pill-icon'} aria-hidden="true">
        {m.icon}
      </span>
      {text ?? m.label}
    </span>
  )
}
