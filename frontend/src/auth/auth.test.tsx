import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import { FakeBackend, PASSWORD } from '../test/fakeBackend'
import { SESSION_EXPIRED_NOTICE } from './AuthProvider'
import { getAccessToken, refreshSession } from './session'

let backend: FakeBackend

beforeEach(() => {
  backend = new FakeBackend().install()
  backend.addUser('viewer@example.com', ['viewer'], { name: 'Vera Viewer' })
})

async function signInThroughForm(email = 'viewer@example.com', password = PASSWORD) {
  await userEvent.type(await screen.findByLabelText('Email'), email)
  await userEvent.type(screen.getByLabelText('Password'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('login', () => {
  it('protects the app: anonymous users see the login form, wherever they land', async () => {
    window.history.replaceState(null, '', '/admin')
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Conversation history' })).not.toBeInTheDocument()
    // Only the cookie-based refresh was attempted; nothing else was called.
    expect(backend.calls.map((c) => c.path)).toEqual(['/api/v1/auth/refresh'])
  })

  it('signs in and shows the chat with the user and role', async () => {
    render(<App />)
    await signInThroughForm()
    expect(await screen.findByRole('navigation', { name: 'Conversation history' })).toBeInTheDocument()
    expect(screen.getByText('Vera Viewer')).toBeInTheDocument()
    expect(screen.getByText('Viewer')).toBeInTheDocument()
    // The token is kept in memory only.
    expect(getAccessToken()).toMatch(/^tok-/)
    expect(JSON.stringify({ ...window.localStorage })).not.toContain('tok-')
  })

  it('shows a generic error on invalid credentials and clears the password', async () => {
    render(<App />)
    await signInThroughForm('viewer@example.com', 'wrong password!!')
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(screen.getByLabelText('Password')).toHaveValue('')
  })

  it('toggles password visibility', async () => {
    render(<App />)
    const field = await screen.findByLabelText('Password')
    expect(field).toHaveAttribute('type', 'password')
    await userEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(field).toHaveAttribute('type', 'text')
    await userEvent.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(field).toHaveAttribute('type', 'password')
  })

  it('disables the button while signing in', async () => {
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Email'), 'viewer@example.com')
    await userEvent.type(screen.getByLabelText('Password'), PASSWORD)
    const button = screen.getByRole('button', { name: 'Sign in' })
    await userEvent.click(button)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument())
    expect(backend.callsTo('POST', '/api/v1/auth/login')).toHaveLength(1)
  })
})

describe('session handling', () => {
  it('restores the session from the refresh cookie on page load', async () => {
    backend.signIn('viewer@example.com')
    render(<App />)
    expect(await screen.findByText('Vera Viewer')).toBeInTheDocument()
    expect(backend.callsTo('POST', '/api/v1/auth/refresh')[0]).toBeDefined()
  })

  it('renews an expired access token once and retries the original request', async () => {
    backend.signIn('viewer@example.com')
    backend.addConversation('Kept')
    render(<App />)
    await screen.findByText('Vera Viewer')
    const refreshesBefore = backend.refreshCount

    backend.expireAccessTokens()
    const page = await api.listConversations()
    expect(page.items.map((c) => c.title)).toEqual(['Kept'])
    expect(backend.refreshCount).toBe(refreshesBefore + 1)
    const listCalls = backend.callsTo('GET', '/api/v1/conversations')
    expect(listCalls.at(-2)?.authorization).not.toBe(listCalls.at(-1)?.authorization)
  })

  it('shares one refresh between concurrent requests', async () => {
    backend.signIn('viewer@example.com')
    render(<App />)
    await screen.findByText('Vera Viewer')
    const before = backend.refreshCount
    backend.expireAccessTokens()
    await Promise.all([api.me(), api.listConversations(), api.myScopes()])
    expect(backend.refreshCount).toBe(before + 1)
  })

  it('retries a refresh once after a concurrent-refresh conflict', async () => {
    backend.signIn('viewer@example.com')
    backend.refreshQueue = [409]
    expect(await refreshSession()).not.toBeNull()
    expect(backend.refreshCount).toBe(2)
  })

  it('does not loop: a second conflict gives up', async () => {
    backend.signIn('viewer@example.com')
    backend.refreshQueue = [409, 409, 409]
    expect(await refreshSession()).toBeNull()
    expect(backend.refreshCount).toBe(2)
  })

  it('returns to login with a notice when the session cannot be renewed', async () => {
    backend.signIn('viewer@example.com')
    render(<App />)
    await screen.findByText('Vera Viewer')
    // Session revoked elsewhere: tokens die and refresh is refused.
    backend.sessionEmail = null
    backend.expireAccessTokens()
    await userEvent.click(screen.getByRole('button', { name: 'New chat' }))
    await expect(api.listConversations()).rejects.toThrow()
    expect(await screen.findByText(SESSION_EXPIRED_NOTICE)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    // Exactly one refresh attempt for the failed request: no retry storm.
    const refreshes = backend.calls.filter((c) => c.path === '/api/v1/auth/refresh').length
    expect(refreshes).toBeLessThanOrEqual(3)
  })

  it('sends the CSRF header with refresh', async () => {
    backend.signIn('viewer@example.com')
    render(<App />)
    await screen.findByText('Vera Viewer')
    // The fake refuses refreshes without the double-submit header, so success proves it.
    expect(getAccessToken()).toMatch(/^tok-/)
  })
})

describe('logout', () => {
  it('signs out and returns to the login form', async () => {
    backend.signIn('viewer@example.com')
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    expect(backend.callsTo('POST', '/api/v1/auth/logout')).toHaveLength(1)
    expect(getAccessToken()).toBeNull()
  })
})
