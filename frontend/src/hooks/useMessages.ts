import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../services/api'
import type { Message } from '../types/api'

export type MessagesStatus = 'idle' | 'loading' | 'ready' | 'error' | 'not_found'

/** Merge by id, keeping server order and appending anything not yet in the page. */
function mergeMessages(base: Message[], extra: Message[]): Message[] {
  const seen = new Set(base.map((m) => m.id))
  return [...base, ...extra.filter((m) => !seen.has(m.id))]
}

export function useMessages(conversationId: string | null) {
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<MessagesStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  // Guards against responses for a conversation the user has already left.
  const currentId = useRef(conversationId)
  currentId.current = conversationId

  const load = useCallback(async (id: string) => {
    setStatus('loading')
    setError(null)
    try {
      const page = await api.listMessages(id)
      if (currentId.current !== id) return
      // A message confirmed while this request was in flight must not be dropped.
      setMessages((prev) => mergeMessages(page.items, prev.filter((m) => m.conversation_id === id)))
      setHasMore(page.has_more)
      setStatus('ready')
    } catch (e) {
      if (currentId.current !== id) return
      if (e instanceof ApiError && e.kind === 'not_found') {
        setStatus('not_found')
      } else {
        setError(e instanceof ApiError ? e.message : 'Could not load messages.')
        setStatus('error')
      }
    }
  }, [])

  useEffect(() => {
    setMessages([])
    setHasMore(false)
    if (conversationId) {
      void load(conversationId)
    } else {
      setStatus('idle')
      setError(null)
    }
  }, [conversationId, load])

  const loadOlder = useCallback(async () => {
    const id = conversationId
    const oldest = messages[0]
    if (!id || !oldest || loadingOlder) return
    setLoadingOlder(true)
    try {
      const page = await api.listMessages(id, { before: oldest.id })
      if (currentId.current !== id) return
      setMessages((prev) => mergeMessages(page.items, prev))
      setHasMore(page.has_more)
    } catch (e) {
      if (currentId.current === id) setError(e instanceof ApiError ? e.message : 'Could not load messages.')
    } finally {
      setLoadingOlder(false)
    }
  }, [conversationId, messages, loadingOlder])

  /** Add a server-confirmed message to the visible conversation. */
  const append = useCallback((message: Message) => {
    if (message.conversation_id !== currentId.current) return
    setMessages((prev) => mergeMessages(prev, [message]))
  }, [])

  const reload = useCallback(() => {
    if (conversationId) void load(conversationId)
  }, [conversationId, load])

  return { messages, status, error, hasMore, loadingOlder, loadOlder, append, reload }
}
