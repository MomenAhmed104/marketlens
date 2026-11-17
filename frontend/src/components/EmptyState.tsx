import type { ReactNode } from 'react'

interface Props {
  title: string
  icon?: ReactNode
  tone?: 'neutral' | 'info' | 'warning'
  children?: ReactNode
  action?: ReactNode
}

export default function EmptyState({ title, icon, tone = 'neutral', children, action }: Props) {
  return (
    <div className={`empty empty-${tone}`}>
      {icon && <div className="empty-icon">{icon}</div>}
      <div className="empty-body">
        <p className="empty-title">{title}</p>
        {children}
        {action && <div className="empty-action">{action}</div>}
      </div>
    </div>
  )
}
