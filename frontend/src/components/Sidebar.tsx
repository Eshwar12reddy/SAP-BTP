import type { LoadStatus } from '../hooks/useConversations'
import type { Conversation } from '../types/api'

interface SidebarProps {
  conversations: Conversation[]
  status: LoadStatus
  error: string | null
  activeId: string | null
  open: boolean
  onSelect: (id: string) => void
  onNewChat: () => void
  onRetry: () => void
}

export function Sidebar({ conversations, status, error, activeId, open, onSelect, onNewChat, onRetry }: SidebarProps) {
  return (
    <aside className="sidebar" data-open={open} aria-label="Conversations">
      <div className="sidebar__header">
        <span className="sidebar__brand">BTP Operations Agent</span>
      </div>
      <button type="button" className="button button--primary sidebar__new" onClick={onNewChat}>
        New chat
      </button>

      <nav className="sidebar__list">
        {status === 'loading' && conversations.length === 0 && <p className="sidebar__hint">Loading conversations…</p>}
        {status === 'error' && (
          <div className="sidebar__hint" role="alert">
            <p>{error}</p>
            <button type="button" className="button button--ghost" onClick={onRetry}>
              Try again
            </button>
          </div>
        )}
        {status === 'ready' && conversations.length === 0 && <p className="sidebar__hint">No conversations yet.</p>}
        <ul>
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                className="sidebar__item"
                aria-current={conversation.id === activeId ? 'page' : undefined}
                onClick={() => onSelect(conversation.id)}
                title={conversation.title}
              >
                {conversation.title}
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  )
}
