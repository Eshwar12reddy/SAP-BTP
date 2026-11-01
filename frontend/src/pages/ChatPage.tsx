import { useEffect, useState } from 'react'
import { Composer } from '../components/Composer'
import { MessageList } from '../components/MessageList'
import { Sidebar } from '../components/Sidebar'
import { useConversations } from '../hooks/useConversations'
import { useMessageSender } from '../hooks/useMessageSender'
import { useMessages } from '../hooks/useMessages'
import { useConversationRoute } from '../routes/conversationRoute'
import { ApiError, api } from '../services/api'

/** Draft key for the "new chat" state, before a conversation exists. */
const NEW_CHAT = 'new'
export const SAVED_NOTICE = 'Message saved. AI responses will be available after AI integration.'

function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError('server', 'Something went wrong. Please try again.', null, null)
}

export function ChatPage() {
  const [activeId, navigate] = useConversationRoute()
  const conversations = useConversations()
  const history = useMessages(activeId)
  const { send, sendingFor } = useMessageSender()

  // Unsent text per conversation; survives failed requests and switching conversations.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [sendError, setSendError] = useState<{ key: string; error: ApiError } | null>(null)
  const [noticeFor, setNoticeFor] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const key = activeId ?? NEW_CHAT
  const draft = drafts[key] ?? ''
  const sending = activeId ? sendingFor === activeId : creating
  const active = conversations.conversations.find((c) => c.id === activeId) ?? null
  const currentError = sendError?.key === key ? sendError.error : null
  const { upsert } = conversations

  // A conversation opened by URL may be older than the loaded sidebar page.
  useEffect(() => {
    if (!activeId || conversations.status !== 'ready' || active) return
    let cancelled = false
    api
      .getConversation(activeId)
      .then((c) => !cancelled && upsert(c))
      .catch(() => undefined) // a missing conversation is reported by the message history
    return () => {
      cancelled = true
    }
  }, [activeId, conversations.status, active, upsert])

  const setDraft = (draftKey: string, value: string) => setDrafts((d) => ({ ...d, [draftKey]: value }))

  const selectConversation = (id: string | null) => {
    navigate(id)
    setNoticeFor(null)
    setSidebarOpen(false)
  }

  const handleSend = async () => {
    const content = draft
    setSendError(null)
    setNoticeFor(null)

    let targetId = activeId
    if (!targetId) {
      setCreating(true)
      try {
        const conversation = await api.createConversation()
        upsert(conversation)
        targetId = conversation.id
        setDrafts(({ [NEW_CHAT]: moved, ...rest }) => ({ ...rest, [conversation.id]: moved ?? content }))
        navigate(conversation.id)
      } catch (e) {
        setSendError({ key: NEW_CHAT, error: asApiError(e) })
        return
      } finally {
        setCreating(false)
      }
    }

    const conversationId = targetId
    try {
      const message = await send(conversationId, content)
      history.append(message)
      setDrafts((d) => ({ ...d, [conversationId]: d[conversationId] === content ? '' : (d[conversationId] ?? '') }))
      setNoticeFor(conversationId)
      // Pick up the new activity time (and auto-title) for the sidebar.
      api.getConversation(conversationId).then(upsert).catch(() => undefined)
    } catch (e) {
      setSendError({ key: conversationId, error: asApiError(e) })
    }
  }

  const notFound = activeId !== null && history.status === 'not_found'
  const showEmpty =
    !notFound && history.messages.length === 0 && (activeId === null || history.status === 'ready')

  return (
    <div className="app">
      <Sidebar
        conversations={conversations.conversations}
        status={conversations.status}
        error={conversations.error}
        activeId={activeId}
        open={sidebarOpen}
        onSelect={selectConversation}
        onNewChat={() => selectConversation(null)}
        onRetry={() => void conversations.reload()}
      />
      {sidebarOpen && <div className="scrim" onClick={() => setSidebarOpen(false)} aria-hidden="true" />}

      <main className="chat">
        <header className="chat__header">
          <button
            type="button"
            className="button button--ghost chat__menu"
            aria-label="Show conversations"
            onClick={() => setSidebarOpen(true)}
          >
            ☰
          </button>
          <h1 className="chat__title">{active?.title ?? (activeId ? '' : 'New conversation')}</h1>
        </header>

        {notFound ? (
          <div className="chat__state">
            <p>This conversation could not be found.</p>
            <button type="button" className="button button--primary" onClick={() => selectConversation(null)}>
              Start a new chat
            </button>
          </div>
        ) : (
          <>
            {history.status === 'loading' && history.messages.length === 0 && (
              <div className="chat__state" role="status">
                Loading messages…
              </div>
            )}
            {history.status === 'error' && (
              <div className="banner banner--error" role="alert">
                <span>{history.error}</span>
                <button type="button" className="button button--ghost" onClick={history.reload}>
                  Try again
                </button>
              </div>
            )}
            {showEmpty ? (
              <div className="chat__state chat__state--empty">
                <h2>Start a conversation</h2>
                <p>Messages are saved to the database. AI responses will be available after AI integration.</p>
              </div>
            ) : (
              history.messages.length > 0 && (
                <MessageList
                  messages={history.messages}
                  hasMore={history.hasMore}
                  loadingOlder={history.loadingOlder}
                  onLoadOlder={() => void history.loadOlder()}
                  footer={
                    noticeFor === activeId && (
                      <p className="notice" role="status">
                        {SAVED_NOTICE}
                      </p>
                    )
                  }
                />
              )
            )}

            <div className="chat__footer">
              {currentError && (
                <div className="banner banner--error" role="alert">
                  <span>
                    {currentError.kind === 'conflict' ? currentError.message : `Message not saved. ${currentError.message}`}
                  </span>
                  {currentError.retryable && draft.trim() && (
                    <button type="button" className="button button--ghost" onClick={() => void handleSend()} disabled={sending}>
                      Retry
                    </button>
                  )}
                </div>
              )}
              <Composer
                value={draft}
                sending={sending}
                autoFocusKey={key}
                onChange={(value) => setDraft(key, value)}
                onSubmit={() => void handleSend()}
              />
            </div>
          </>
        )}
      </main>
    </div>
  )
}
