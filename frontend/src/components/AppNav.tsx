import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { navigateTo, type AppView } from '../routes/router'

export const ROLE_LABELS: Record<string, string> = {
  viewer: 'Viewer',
  operator: 'Operator',
  approver: 'Approver',
  administrator: 'Administrator',
  knowledge_reader: 'Knowledge Reader',
  knowledge_manager: 'Knowledge Manager',
}

/** Current user, role badges and navigation; used in the chat sidebar and page header. */
export function UserMenu({ current, compact = false }: { current: AppView; compact?: boolean }) {
  const { me, can, logout } = useAuth()
  if (!me) return null
  const links: { view: AppView; label: string; path: string; visible: boolean }[] = [
    { view: 'chat', label: 'Chat', path: '/', visible: true },
    { view: 'profile', label: 'Profile', path: '/profile', visible: true },
    { view: 'admin', label: 'Administration', path: '/admin', visible: can('users:read') },
  ]
  return (
    <div className={compact ? 'user-menu user-menu--compact' : 'user-menu'}>
      <div className="user-menu__identity">
        <span className="user-menu__name">{me.user.display_name}</span>
        <span className="user-menu__roles">
          {me.roles.length ? me.roles.map((r) => ROLE_LABELS[r] ?? r).join(' · ') : 'No roles assigned'}
        </span>
      </div>
      <nav className="user-menu__links" aria-label="Main">
        {links
          .filter((l) => l.visible && l.view !== current)
          .map((l) => (
            <button key={l.view} type="button" className="link-button" onClick={() => navigateTo(l.path)}>
              {l.label}
            </button>
          ))}
        <button type="button" className="link-button" onClick={() => void logout()}>
          Sign out
        </button>
      </nav>
    </div>
  )
}

export function PageShell({ view, title, children }: { view: AppView; title: string; children: ReactNode }) {
  return (
    <div className="page">
      <header className="page__header">
        <span className="page__brand">BTP Operations Agent</span>
        <UserMenu current={view} compact />
      </header>
      <main className="page__body">
        <h1 className="page__title">{title}</h1>
        {children}
      </main>
    </div>
  )
}
