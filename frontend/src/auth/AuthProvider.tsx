import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, api } from '../services/api'
import type { Me } from '../types/api'
import { onSessionExpired, refreshSession, setAccessToken } from './session'

type Status = 'loading' | 'authenticated' | 'anonymous'

interface AuthContextValue {
  status: Status
  me: Me | null
  /** Shown on the login screen after a forced sign-out. */
  notice: string | null
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  logoutAll: () => Promise<void>
  reload: () => Promise<void>
  can: (permission: string) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export const SESSION_EXPIRED_NOTICE = 'Your session has expired. Please sign in again.'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [me, setMe] = useState<Me | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const signedOut = useCallback((message: string | null) => {
    setAccessToken(null)
    setMe(null)
    setNotice(message)
    setStatus('anonymous')
  }, [])

  const reload = useCallback(async () => {
    setMe(await api.me())
  }, [])

  // Restore an existing session from the refresh cookie on page load.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const restored = await refreshSession()
      if (cancelled) return
      if (!restored) return signedOut(null)
      try {
        const profile = await api.me()
        if (!cancelled) {
          setMe(profile)
          setStatus('authenticated')
        }
      } catch {
        if (!cancelled) signedOut(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [signedOut])

  useEffect(() => onSessionExpired(() => signedOut(SESSION_EXPIRED_NOTICE)), [signedOut])

  const login = useCallback(async (email: string, password: string) => {
    await api.login(email, password)
    setMe(await api.me())
    setNotice(null)
    setStatus('authenticated')
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch (e) {
      // Signing out locally must not depend on the network.
      if (!(e instanceof ApiError)) throw e
    }
    signedOut(null)
  }, [signedOut])

  const logoutAll = useCallback(async () => {
    await api.logoutAll()
    signedOut('You have been signed out on all devices.')
  }, [signedOut])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      me,
      notice,
      login,
      logout,
      logoutAll,
      reload,
      can: (permission: string) => me?.permissions.includes(permission) ?? false,
    }),
    [status, me, notice, login, logout, logoutAll, reload],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
