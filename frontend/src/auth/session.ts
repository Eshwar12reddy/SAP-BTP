/**
 * Session state shared by the API client and the auth provider.
 *
 * - The access token lives only in this module's memory (never in localStorage).
 * - The refresh token is an HttpOnly cookie the browser sends to /api/v1/auth/*.
 * - Refreshes are single-flight within a tab and serialized across tabs with the
 *   Web Locks API, so tabs do not race each other into replay detection.
 */
import type { TokenResponse } from '../types/api'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const REFRESH_URL = `${API_BASE}/api/v1/auth/refresh`
const CSRF_COOKIE = 'btp_csrf'
const LOCK_NAME = 'btp-auth-refresh'

let accessToken: string | null = null
let inFlight: Promise<TokenResponse | null> | null = null
const expiredListeners = new Set<() => void>()

export const credentials: RequestCredentials = API_BASE ? 'include' : 'same-origin'

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function readCsrfToken(): string {
  const match = document.cookie.split('; ').find((c) => c.startsWith(`${CSRF_COOKIE}=`))
  return match ? decodeURIComponent(match.slice(CSRF_COOKIE.length + 1)) : ''
}

/** Called when the session can no longer be renewed (the user must sign in again). */
export function onSessionExpired(listener: () => void): () => void {
  expiredListeners.add(listener)
  return () => expiredListeners.delete(listener)
}

export function notifySessionExpired(): void {
  accessToken = null
  expiredListeners.forEach((listener) => listener())
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function refreshOnce(): Promise<TokenResponse | null> {
  // No CSRF cookie means no session was ever established in this browser.
  if (!readCsrfToken()) return null
  // A 409 means another request rotated the token a moment ago; the browser already
  // holds the new cookie, so one retry is enough. Never loop.
  for (let attempt = 0; attempt < 2; attempt++) {
    let response: Response
    try {
      response = await fetch(REFRESH_URL, {
        method: 'POST',
        credentials,
        headers: { 'X-CSRF-Token': readCsrfToken(), Accept: 'application/json' },
      })
    } catch {
      return null
    }
    if (response.ok) {
      const data = (await response.json()) as TokenResponse
      accessToken = data.access_token
      return data
    }
    if (response.status !== 409) return null
    await sleep(250)
  }
  return null
}

/** Renew the access token. Concurrent callers share one request. */
export function refreshSession(): Promise<TokenResponse | null> {
  if (!inFlight) {
    const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
    const run = locks?.request ? locks.request(LOCK_NAME, refreshOnce) : refreshOnce()
    inFlight = (run as Promise<TokenResponse | null>).finally(() => {
      inFlight = null
    })
  }
  return inFlight
}
