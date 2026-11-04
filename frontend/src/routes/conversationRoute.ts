import { useCallback, useEffect, useState } from 'react'
import { navigateTo, subscribeToNavigation } from './router'

/**
 * Minimal URL routing: "/" (no selection) and "/c/:conversationId".
 * Keeping the active conversation in the URL lets a page refresh reopen it.
 */
const ROUTE = /^\/c\/([0-9a-fA-F-]{36})\/?$/

export function conversationIdFromPath(pathname: string): string | null {
  return ROUTE.exec(pathname)?.[1]?.toLowerCase() ?? null
}

export function pathForConversation(id: string | null): string {
  return id ? `/c/${id}` : '/'
}

export function useConversationRoute(): [string | null, (id: string | null) => void] {
  const [activeId, setActiveId] = useState(() => conversationIdFromPath(window.location.pathname))

  useEffect(() => subscribeToNavigation(() => setActiveId(conversationIdFromPath(window.location.pathname))), [])

  const navigate = useCallback((id: string | null) => {
    navigateTo(pathForConversation(id))
    setActiveId(id)
  }, [])

  return [activeId, navigate]
}
