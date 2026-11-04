import { useEffect, useRef, type ReactNode } from 'react'
import type { Message } from '../types/api'
import { Markdown } from './Markdown'

interface MessageListProps {
  messages: Message[]
  hasMore: boolean
  loadingOlder: boolean
  onLoadOlder: () => void
  footer?: ReactNode
  /** Extra content under a message, e.g. the reply state of a user message. */
  renderStatus?: (message: Message) => ReactNode
}

const timeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const SOURCE_SEPARATOR = '\n\n— '

/** The agent ends each reply with a source line written by the backend (not the model). */
export function splitSource(message: Message): { body: string; source: string | null } {
  const at = message.role === 'assistant' ? message.content.lastIndexOf(SOURCE_SEPARATOR) : -1
  if (at < 0) return { body: message.content, source: null }
  return { body: message.content.slice(0, at), source: message.content.slice(at + SOURCE_SEPARATOR.length) }
}

export function MessageList({ messages, hasMore, loadingOlder, onLoadOlder, footer, renderStatus }: MessageListProps) {
  const scroller = useRef<HTMLDivElement>(null)
  const lastId = messages.at(-1)?.id

  // Follow the conversation when a new latest message arrives.
  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lastId])

  return (
    <div className="messages" ref={scroller}>
      <div className="messages__inner">
        {hasMore && (
          <button type="button" className="button button--ghost messages__older" onClick={onLoadOlder} disabled={loadingOlder}>
            {loadingOlder ? 'Loading…' : 'Load earlier messages'}
          </button>
        )}
        <ol className="messages__list" aria-label="Message history">
          {messages.map((message) => {
            const { body, source } = splitSource(message)
            return (
              <li key={message.id} className={`message message--${message.role}`}>
                {message.role === 'assistant' ? (
                  // Safe Markdown: React elements only, raw HTML skipped, unsafe URLs removed.
                  <div className="message__body message__body--markdown">
                    <Markdown text={body} />
                  </div>
                ) : (
                  // User text is shown exactly as typed, never interpreted.
                  <div className="message__body">{body}</div>
                )}
                {source && <p className="message__source">{source}</p>}
                <time className="message__time" dateTime={message.created_at}>
                  {timeFormat.format(new Date(message.created_at))}
                </time>
                {renderStatus?.(message)}
              </li>
            )
          })}
        </ol>
        {footer}
      </div>
    </div>
  )
}
