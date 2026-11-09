import { useEffect, useState } from 'react'

/** Fired after in-app navigation, so every route hook re-reads the URL. */
const NAVIGATE_EVENT = 'btp:navigate'

export type AppView = 'chat' | 'profile' | 'admin' | 'btp' | 'knowledge' | 'recommendations' | 'architecture' | 'sizing' | 'infrastructure'

export function navigateTo(path: string): void {
  if (window.location.pathname !== path) window.history.pushState(null, '', path)
  window.dispatchEvent(new Event(NAVIGATE_EVENT))
}

export function subscribeToNavigation(listener: () => void): () => void {
  window.addEventListener('popstate', listener)
  window.addEventListener(NAVIGATE_EVENT, listener)
  return () => {
    window.removeEventListener('popstate', listener)
    window.removeEventListener(NAVIGATE_EVENT, listener)
  }
}

export function viewForPath(pathname: string): AppView {
  if (pathname === '/profile') return 'profile'
  if (pathname === '/admin') return 'admin'
  if (pathname === '/btp') return 'btp'
  if (pathname === '/knowledge') return 'knowledge'
  if (pathname === '/recommendations') return 'recommendations'
  if (pathname === '/architecture') return 'architecture'
  if (pathname === '/sizing') return 'sizing'
  if (pathname === '/infrastructure-plans') return 'infrastructure'
  return 'chat'
}

export function useView(): AppView {
  const [view, setView] = useState(() => viewForPath(window.location.pathname))
  useEffect(() => subscribeToNavigation(() => setView(viewForPath(window.location.pathname))), [])
  return view
}
