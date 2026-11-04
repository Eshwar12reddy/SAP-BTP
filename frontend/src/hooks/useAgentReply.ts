import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../services/api'
import type { AgentReply } from '../types/api'

export const POLL_INTERVAL_MS = 3_000
/** About four minutes: longer than the server's limit for one reply. */
export const POLL_LIMIT = 80

/** Per saved user message. */
export type ReplyState =
  | { kind: 'requesting'; conversationId: string }
  | { kind: 'generating'; conversationId: string }
  | { kind: 'stalled'; conversationId: string }
  | { kind: 'failed'; conversationId: string; error: ApiError | null }

function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError('server', 'Something went wrong. Please try again.', null, null)
}

/** Retrying cannot help when access or the message itself is the problem. */
export function canRetryReply(error: ApiError | null): boolean {
  return !error || !['unauthorized', 'forbidden', 'not_found', 'validation'].includes(error.kind)
}

/** The request may have reached the server: ask how it went rather than reporting a failure. */
function outcomeUnknown(error: ApiError): boolean {
  return error.code === 'reply_in_progress' || error.kind === 'timeout' || error.kind === 'network'
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const { [key]: _removed, ...rest } = record
  return rest
}

/**
 * Requests and tracks agent replies for saved user messages. Never more than one request
 * or status poll per message; polling is read-only (it never starts an AI call), bounded,
 * and stopped on unmount. Completed replies are handed to ``onReply``.
 */
export function useAgentReply(onReply: (reply: AgentReply) => void) {
  const [states, setStates] = useState<Record<string, ReplyState>>({})
  const busy = useRef(new Set<string>())
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const checks = useRef(new Map<string, () => void>())
  const onReplyRef = useRef(onReply)
  onReplyRef.current = onReply

  useEffect(() => {
    const pending = timers.current
    const running = busy.current
    return () => {
      // Stop polling; a later mount starts again from the server state.
      for (const [messageId, timer] of pending) {
        clearTimeout(timer)
        running.delete(messageId)
      }
      pending.clear()
    }
  }, [])

  const setState = useCallback((messageId: string, state: ReplyState | null) => {
    setStates((s) => (state ? { ...s, [messageId]: state } : without(s, messageId)))
  }, [])

  const stop = useCallback((messageId: string) => {
    busy.current.delete(messageId)
    checks.current.delete(messageId)
    clearTimeout(timers.current.get(messageId))
    timers.current.delete(messageId)
  }, [])

  /** Poll the reply status of a message whose reply may be in progress on the server. */
  const watch = useCallback(
    (conversationId: string, messageId: string, { immediate = false } = {}) => {
      if (busy.current.has(messageId)) return
      busy.current.add(messageId)
      setState(messageId, { kind: 'generating', conversationId })
      let attempts = 0

      const check = async () => {
        timers.current.delete(messageId)
        attempts += 1
        try {
          const status = await api.replyStatus(conversationId, messageId)
          if (!busy.current.has(messageId)) return
          if (status.status === 'completed' && status.reply) {
            stop(messageId)
            setState(messageId, null)
            onReplyRef.current(status.reply)
            return
          }
          if (status.status === 'failed' || status.status === 'not_requested') {
            stop(messageId)
            setState(messageId, { kind: 'failed', conversationId, error: null })
            return
          }
        } catch (e) {
          const error = toApiError(e)
          if (!error.retryable) {
            stop(messageId)
            setState(messageId, { kind: 'failed', conversationId, error })
            return
          }
        }
        if (!busy.current.has(messageId)) return
        if (attempts >= POLL_LIMIT) {
          stop(messageId)
          setState(messageId, { kind: 'stalled', conversationId })
          return
        }
        timers.current.set(messageId, setTimeout(() => void check(), POLL_INTERVAL_MS))
      }

      checks.current.set(messageId, () => void check())
      timers.current.set(messageId, setTimeout(() => void check(), immediate ? 0 : POLL_INTERVAL_MS))
    },
    [setState, stop],
  )

  /** Check right away (the "Check now" button); restarts a stalled watch. */
  const checkNow = useCallback(
    (conversationId: string, messageId: string) => {
      const check = checks.current.get(messageId)
      if (check && timers.current.has(messageId)) {
        clearTimeout(timers.current.get(messageId))
        timers.current.delete(messageId)
        check()
      } else {
        watch(conversationId, messageId, { immediate: true })
      }
    },
    [watch],
  )

  /** Ask the agent to answer a saved message. The server never stores a second reply. */
  const requestReply = useCallback(
    async (conversationId: string, messageId: string) => {
      if (busy.current.has(messageId)) return
      busy.current.add(messageId)
      setState(messageId, { kind: 'requesting', conversationId })
      try {
        const reply = await api.requestReply(conversationId, messageId)
        busy.current.delete(messageId)
        setState(messageId, null)
        onReplyRef.current(reply)
      } catch (e) {
        busy.current.delete(messageId)
        const error = toApiError(e)
        if (outcomeUnknown(error)) {
          watch(conversationId, messageId)
        } else {
          setState(messageId, { kind: 'failed', conversationId, error })
        }
      }
    },
    [setState, watch],
  )

  return { states, requestReply, watch, checkNow }
}
