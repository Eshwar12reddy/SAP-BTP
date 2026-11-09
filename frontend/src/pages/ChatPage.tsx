import { useCallback, useEffect, useMemo, useState } from 'react'
import { Composer } from '../components/Composer'
import { ContextSelector, contextLabel, useBtpContext } from '../components/ContextSelector'
import { MessageList } from '../components/MessageList'
import { Sidebar } from '../components/Sidebar'
import { canRetryReply, useAgentReply } from '../hooks/useAgentReply'
import { useConversations } from '../hooks/useConversations'
import { useMessageSender } from '../hooks/useMessageSender'
import { useMessages } from '../hooks/useMessages'
import { useConversationRoute } from '../routes/conversationRoute'
import { ApiError, api } from '../services/api'
import type { AgentReply, ApprovalDecision, InvestigationStatus, Message } from '../types/api'

/** Draft key for the "new chat" state, before a conversation exists. */
const NEW_CHAT = 'new'
export const REPLYING_NOTICE = 'Generating a reply…'
export const STILL_GENERATING = 'Reply still generating…'
export const REPLY_INCOMPLETE = 'The reply did not complete.'

function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError('server', 'Something went wrong. Please try again.', null, null)
}

export function ChatPage() {
  const [activeId, navigate] = useConversationRoute()
  const conversations = useConversations()
  const history = useMessages(activeId)
  const { send, sendingFor } = useMessageSender()
  const btp = useBtpContext()
  const { upsert } = conversations
  const { append } = history
  const [investigation, setInvestigation] = useState<InvestigationStatus | null>(null)
  const [approvalBusy, setApprovalBusy] = useState(false)

  const refreshInvestigation = useCallback(() => {
    if (!activeId) {
      setInvestigation(null)
      return
    }
    api.investigationStatus(activeId).then(setInvestigation).catch(() => setInvestigation(null))
  }, [activeId])

  useEffect(() => {
    refreshInvestigation()
  }, [refreshInvestigation])

  const onReply = useCallback(
    (reply: AgentReply) => {
      // Ignored if the user has moved on; reopening the conversation loads it from the server.
      append(reply.message)
      api.getConversation(reply.message.conversation_id).then(upsert).catch(() => undefined)
      refreshInvestigation()
    },
    [append, refreshInvestigation, upsert],
  )
  const { states: replyStates, requestReply, watch, checkNow } = useAgentReply(onReply)

  // Unsent text per conversation; survives failed requests and switching conversations.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [sendError, setSendError] = useState<{ key: string; error: ApiError } | null>(null)
  const [creating, setCreating] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const key = activeId ?? NEW_CHAT
  const draft = drafts[key] ?? ''
  const replying = Object.values(replyStates).some((s) => s.kind === 'requesting' && s.conversationId === activeId)
  const saving = activeId ? sendingFor === activeId : creating
  const sending = saving || replying
  const active = conversations.conversations.find((c) => c.id === activeId) ?? null
  const currentError = sendError?.key === key ? sendError.error : null

  /** User messages that already have their reply loaded. */
  const answered = useMemo(
    () => new Set(history.messages.map((m) => m.reply_to_message_id).filter(Boolean)),
    [history.messages],
  )

  // A reply the server reports as still generating (e.g. after a refresh) is watched with
  // read-only status checks. Loading a conversation never starts an AI call.
  useEffect(() => {
    for (const m of history.messages) {
      if (m.role === 'user' && m.reply_status === 'generating' && !answered.has(m.id) && !replyStates[m.id]) {
        watch(m.conversation_id, m.id)
      }
    }
  }, [history.messages, answered, replyStates, watch])

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
    setSidebarOpen(false)
  }

  /** Reply state under a user message: still generating, or failed with a retry. */
  const renderReplyStatus = (message: Message) => {
    if (message.role !== 'user' || answered.has(message.id)) return null
    const state = replyStates[message.id]
    const check = () => checkNow(message.conversation_id, message.id)
    if (state?.kind === 'generating' || (!state && message.reply_status === 'generating')) {
      return (
        <div className="reply-status" role="status">
          <span>{STILL_GENERATING}</span>
          <button type="button" className="button button--ghost" onClick={check}>
            Check now
          </button>
        </div>
      )
    }
    if (state?.kind === 'stalled') {
      return (
        <div className="reply-status" role="status">
          <span>The reply is taking longer than expected.</span>
          <button type="button" className="button button--ghost" onClick={check}>
            Check again
          </button>
        </div>
      )
    }
    const error = state?.kind === 'failed' ? state.error : null
    const failed = state?.kind === 'failed' || (!state && message.reply_status === 'failed')
    if (!failed) return null
    return (
      <div className="reply-status reply-status--error" role="alert">
        <span>AI reply failed. {error?.message ?? REPLY_INCOMPLETE}</span>
        {canRetryReply(error) && (
          <button
            type="button"
            className="button button--ghost"
            onClick={() => void requestReply(message.conversation_id, message.id)}
          >
            Retry reply
          </button>
        )}
      </div>
    )
  }

  const handleSend = async () => {
    const content = draft
    setSendError(null)

    let targetId = activeId
    if (!targetId) {
      setCreating(true)
      try {
        const conversation = await api.createConversation(btp.selectedContext)
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
    let messageId: string
    try {
      const message = await send(conversationId, content)
      append(message)
      messageId = message.id
      setDrafts((d) => ({ ...d, [conversationId]: d[conversationId] === content ? '' : (d[conversationId] ?? '') }))
      // Pick up the new activity time (and auto-title) for the sidebar.
      api.getConversation(conversationId).then(upsert).catch(() => undefined)
    } catch (e) {
      setSendError({ key: conversationId, error: asApiError(e) })
      return
    }
    // Only a message the server has saved is ever answered.
    await requestReply(conversationId, messageId)
    refreshInvestigation()
  }

  const chooseApproval = async (decision: ApprovalDecision) => {
    const approval = investigation?.pending_approval
    if (!activeId || !approval || approval.status !== 'pending') return
    setApprovalBusy(true)
    try {
      await api.decideInvestigationApproval(activeId, approval.id, decision)
      refreshInvestigation()
    } finally {
      setApprovalBusy(false)
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
          {activeId === null ? (
            <ContextSelector context={btp} />
          ) : (
            active &&
            contextLabel(btp.scopes, active) && <span className="badge badge--muted chat__context">{contextLabel(btp.scopes, active)}</span>
          )}
        </header>

        {investigation?.pending_approval?.status === 'pending' && (
          <section className="investigation-panel" aria-label="Human approval required">
            <h2>Approval required</h2>
            <p>{investigation.pending_approval.operation_summary}</p>
            <p className="notice">No infrastructure change has been made. The current phase has no executor.</p>
            <div className="investigation-panel__actions">
              <button type="button" className="button button--primary" disabled={approvalBusy} onClick={() => void chooseApproval('execute')}>
                Yes — Execute
              </button>
              <button type="button" className="button button--ghost" disabled={approvalBusy} onClick={() => void chooseApproval('cancel')}>
                No — Cancel
              </button>
              <button type="button" className="button button--ghost" disabled={approvalBusy} onClick={() => void chooseApproval('other_approach')}>
                Other Approach
              </button>
              <button type="button" className="button button--ghost" disabled={approvalBusy} onClick={() => void chooseApproval('explain_command')}>
                Explain Command
              </button>
            </div>
          </section>
        )}

        {investigation?.pending_approval && investigation.pending_approval.status !== 'pending' && (
          <section className="investigation-panel" aria-label="Approval result" role="status">
            <h2>Approval {investigation.pending_approval.status.replaceAll('_', ' ')}</h2>
            <p>{investigation.pending_approval.result_summary ?? 'No further action is available for this proposal.'}</p>
          </section>
        )}

        {investigation && (investigation.findings ?? []).length > 0 && (
          <section className="investigation-panel" aria-label="Investigation findings">
            <h2>Investigation findings</h2>
            {(investigation.findings ?? []).map((finding) => (
              <article key={finding.finding_id}>
                <strong>{finding.severity} · {finding.confidence}</strong>
                <p>{finding.explanation}</p>
                <small>Evidence: {finding.evidence_refs.join(', ') || 'none'}</small>
              </article>
            ))}
          </section>
        )}

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
                <p>
                  Ask about SAP BTP concepts, your accounts and environments, architecture or sizing. Each answer says
                  where its information came from.
                </p>
              </div>
            ) : (
              history.messages.length > 0 && (
                <MessageList
                  messages={history.messages}
                  hasMore={history.hasMore}
                  loadingOlder={history.loadingOlder}
                  onLoadOlder={() => void history.loadOlder()}
                  renderStatus={renderReplyStatus}
                  footer={
                    replying && (
                      <p className="notice" role="status">
                        {REPLYING_NOTICE}
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
                busyLabel={replying ? 'Replying…' : 'Saving…'}
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
