import type { ReactNode } from 'react'

export type StatTone = 'default' | 'accent' | 'attention' | 'muted' | 'success'

interface Props {
  label: string
  value: ReactNode
  detail?: ReactNode
  tone?: StatTone
  source: string // backend field(s) behind the number; shown as a tooltip
}

export default function StatTile({ label, value, detail, tone = 'default', source }: Props) {
  return (
    <div className={`stat stat-${tone}`} title={`Source: ${source}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  )
}
