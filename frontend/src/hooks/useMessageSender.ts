import { useCallback, useRef, useState } from 'react'
import { ApiError, api } from '../services/api'
import type { Message } from '../types/api'

interface PendingKey {
  key: string
  clientMessageId: string
}

function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError('server', 'Something went wrong. Please try again.', null, null)
}

/**
 * Sends messages with a stable idempotency key: retrying the same text in the same
 * conversation reuses its client_message_id, so the backend never stores a duplicate.
 */
export function useMessageSender() {
  const pending = useRef<PendingKey | null>(null)
  const [sendingFor, setSendingFor] = useState<string | null>(null)

  const send = useCallback(async (conversationId: string, content: string): Promise<Message> => {
    const key = `${conversationId}\u0000${content}`
    if (pending.current?.key !== key) {
      pending.current = { key, clientMessageId: crypto.randomUUID() }
    }
    const { clientMessageId } = pending.current
    setSendingFor(conversationId)
    try {
      const message = await api.sendMessage(conversationId, content, clientMessageId)
      pending.current = null
      return message
    } catch (e) {
      const error = toApiError(e)
      // Keep the key only when the same request may be retried safely.
      if (!error.retryable) pending.current = null
      throw error
    } finally {
      setSendingFor(null)
    }
  }, [])

  return { send, sendingFor }
}
