import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../services/api'
import type { Conversation } from '../types/api'

export type LoadStatus = 'loading' | 'ready' | 'error'

function byActivity(a: Conversation, b: Conversation): number {
  return b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id)
}

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [status, setStatus] = useState<LoadStatus>('loading')
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setStatus('loading')
    setError(null)
    try {
      const page = await api.listConversations()
      setConversations(page.items)
      setStatus('ready')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load conversations.')
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  /** Insert or replace a conversation, keeping most-recent-activity order. */
  const upsert = useCallback((conversation: Conversation) => {
    setConversations((prev) => [conversation, ...prev.filter((c) => c.id !== conversation.id)].sort(byActivity))
  }, [])

  return { conversations, status, error, reload, upsert }
}
