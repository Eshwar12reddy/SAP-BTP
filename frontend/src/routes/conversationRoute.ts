import { useCallback, useEffect, useState } from 'react'

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

  useEffect(() => {
    const onPopState = () => setActiveId(conversationIdFromPath(window.location.pathname))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = useCallback((id: string | null) => {
    const path = pathForConversation(id)
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
    setActiveId(id)
  }, [])

  return [activeId, navigate]
}
