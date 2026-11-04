import { useEffect, useRef } from 'react'

export const MAX_MESSAGE_LENGTH = 32_000

interface ComposerProps {
  value: string
  sending: boolean
  onChange: (value: string) => void
  onSubmit: () => void
  autoFocusKey: string
  /** Button text while ``sending`` is true. */
  busyLabel?: string
}

export function Composer({ value, sending, onChange, onSubmit, autoFocusKey, busyLabel = 'Saving…' }: ComposerProps) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  const tooLong = value.length > MAX_MESSAGE_LENGTH
  const canSend = !sending && value.trim().length > 0 && !tooLong

  useEffect(() => {
    textarea.current?.focus()
  }, [autoFocusKey])

  // Grow with the content up to the CSS max-height.
  useEffect(() => {
    const el = textarea.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  const submit = () => {
    if (canSend) onSubmit()
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <label htmlFor="composer-input" className="visually-hidden">
        Message
      </label>
      <textarea
        id="composer-input"
        ref={textarea}
        className="composer__input"
        rows={1}
        placeholder="Write a message…  (Enter to send, Shift+Enter for a new line)"
        value={value}
        readOnly={sending}
        aria-invalid={tooLong}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            submit()
          }
        }}
      />
      <div className="composer__actions">
        {tooLong && (
          <span className="composer__hint" role="status">
            {value.length.toLocaleString()} / {MAX_MESSAGE_LENGTH.toLocaleString()} characters
          </span>
        )}
        <button type="submit" className="button button--primary" disabled={!canSend}>
          {sending ? busyLabel : 'Send'}
        </button>
      </div>
    </form>
  )
}
