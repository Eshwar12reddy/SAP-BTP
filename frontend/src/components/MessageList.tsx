import { useEffect, useRef, type ReactNode } from 'react'
import type { Message } from '../types/api'

interface MessageListProps {
  messages: Message[]
  hasMore: boolean
  loadingOlder: boolean
  onLoadOlder: () => void
  footer?: ReactNode
}

const timeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export function MessageList({ messages, hasMore, loadingOlder, onLoadOlder, footer }: MessageListProps) {
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
          {messages.map((message) => (
            <li key={message.id} className={`message message--${message.role}`}>
              {/* Rendered as text, never HTML. */}
              <div className="message__body">{message.content}</div>
              <time className="message__time" dateTime={message.created_at}>
                {timeFormat.format(new Date(message.created_at))}
              </time>
            </li>
          ))}
        </ol>
        {footer}
      </div>
    </div>
  )
}
