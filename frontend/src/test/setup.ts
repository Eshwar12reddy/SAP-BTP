import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { setAccessToken } from '../auth/session'

afterEach(() => {
  cleanup()
  setAccessToken(null)
  window.history.replaceState(null, '', '/')
  document.cookie = 'btp_csrf=; max-age=0; path=/'
  try {
    window.localStorage.clear()
  } catch {
    // ignore
  }
})
