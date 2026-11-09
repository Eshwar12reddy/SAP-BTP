import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import { FakeBackend } from '../test/fakeBackend'

let backend: FakeBackend

beforeEach(() => {
  backend = new FakeBackend().install()
})

function renderAs(email: string, roles: Parameters<FakeBackend['addUser']>[1], path = '/') {
  backend.addUser(email, roles, { name: email.split('@')[0] })
  backend.signIn(email)
  window.history.replaceState(null, '', path)
  render(<App />)
}

describe('role-aware UI', () => {
  it('viewers get chat and profile, but no administration link', async () => {
    renderAs('viewer@example.com', ['viewer'])
    expect(await screen.findByRole('navigation', { name: 'Conversation history' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Profile' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Administration' })).not.toBeInTheDocument()
  })

  it('viewers who open /admin directly are refused', async () => {
    renderAs('viewer@example.com', ['viewer'], '/admin')
    expect(await screen.findByRole('alert')).toHaveTextContent('You do not have permission to manage users.')
    expect(backend.calls.some((c) => c.path.startsWith('/api/v1/admin'))).toBe(false)
  })

  it('administrators without a BTP role see administration but no chat', async () => {
    renderAs('admin@example.com', ['administrator'])
    expect(await screen.findByText(/Your roles do not include chat access/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Administration' })).toBeInTheDocument()
    expect(backend.calls.some((c) => c.path.startsWith('/api/v1/conversations'))).toBe(false)
  })

  it('offers the readable BTP contexts and creates conversations in the chosen one', async () => {
    renderAs('viewer@example.com', ['viewer'])
    const selector = await screen.findByRole('combobox', { name: 'BTP context' })
    expect(within(selector).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'No BTP context',
      'Acme EU (subaccount)',
      'Development · cloudfoundry (Acme EU)',
    ])
    await userEvent.selectOptions(selector, 'environment:env-1')
    await userEvent.type(screen.getByLabelText('Message'), 'Check Development{Enter}')
    await screen.findByText(backend.replyText)
    expect(backend.callsTo('POST', '/api/v1/conversations')[0].body).toEqual({ btp_environment_id: 'env-1' })
    expect(await screen.findByText('Development', { selector: '.chat__context' })).toBeInTheDocument()
  })
})

describe('user management', () => {
  beforeEach(() => {
    backend.addUser('operator@example.com', ['operator'], { name: 'Olive Operator' })
  })

  it('lists and searches users', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    const list = await screen.findByRole('list', { name: 'Users' })
    expect(await within(list).findByText('Olive Operator')).toBeInTheDocument()
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search users' }), 'olive')
    // The search is debounced.
    await waitFor(() => expect(backend.calls.some((c) => c.path.includes('search=olive'))).toBe(true))
    expect(await within(list).findByText('Olive Operator')).toBeInTheDocument()
  })

  it('creates a user', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByRole('button', { name: 'New user' }))
    await userEvent.type(screen.getByLabelText('Email'), 'new@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Nina New')
    await userEvent.type(screen.getByLabelText('Initial password'), 'short')
    await userEvent.click(screen.getByRole('button', { name: 'Create user' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters')
    expect(backend.callsTo('POST', '/api/v1/admin/users')).toHaveLength(0)

    await userEvent.type(screen.getByLabelText('Initial password'), ' but now long enough')
    await userEvent.click(screen.getByRole('button', { name: 'Create user' }))
    expect(await screen.findByRole('heading', { name: 'Nina New' })).toBeInTheDocument()
    expect(backend.users.has('new@example.com')).toBe(true)
  })

  it('shows server errors such as duplicate emails', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByRole('button', { name: 'New user' }))
    await userEvent.type(screen.getByLabelText('Email'), 'operator@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Dup')
    await userEvent.type(screen.getByLabelText('Initial password'), 'long enough password')
    await userEvent.click(screen.getByRole('button', { name: 'Create user' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A user with this email already exists.')
  })

  it('disables and re-enables a user, and revokes sessions', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByText('Olive Operator'))
    await userEvent.click(await screen.findByRole('button', { name: 'Disable account' }))
    expect(await screen.findByRole('button', { name: 'Enable account' })).toBeInTheDocument()
    expect(backend.users.get('operator@example.com')!.user.is_active).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Enable account' }))
    expect(await screen.findByRole('button', { name: 'Disable account' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Revoke sessions' }))
    expect(await screen.findByRole('status')).toHaveTextContent('1 session revoked.')
  })

  it('assigns and revokes roles', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByText('Olive Operator'))
    const form = await screen.findByRole('form', { name: 'Assign role' })
    await userEvent.selectOptions(within(form).getByLabelText('Role'), 'viewer')
    await userEvent.selectOptions(within(form).getByLabelText('Scope'), 'environment:env-1')
    // Environment scopes have no children to include.
    expect(within(form).getByRole('checkbox')).toBeDisabled()
    await userEvent.click(within(form).getByRole('button', { name: 'Assign' }))

    const row = await screen.findByRole('row', { name: /Viewer.*Development/ })
    expect(backend.callsTo('POST', '/api/v1/admin/users/')[0].body).toMatchObject({
      role: 'viewer',
      scope_type: 'environment',
      scope_id: 'env-1',
      include_descendants: false,
    })
    await userEvent.click(within(row).getByRole('button', { name: 'Revoke' }))
    await expectRowGone(/Viewer.*Development/)
  })

  it('assigns a knowledge role at global knowledge scope', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByText('Olive Operator'))
    const form = await screen.findByRole('form', { name: 'Assign role' })
    await userEvent.selectOptions(within(form).getByLabelText('Role'), 'knowledge_manager')
    expect(within(form).getByRole('option', { name: 'Platform (global knowledge)' })).toBeInTheDocument()
    await userEvent.selectOptions(within(form).getByLabelText('Scope'), 'platform:')
    await userEvent.click(within(form).getByRole('button', { name: 'Assign' }))
    expect(backend.callsTo('POST', '/api/v1/admin/users/')[0].body).toMatchObject({
      role: 'knowledge_manager',
      scope_type: 'platform',
      scope_id: null,
      include_descendants: false,
    })
  })

  it('does not offer self-management', async () => {
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByText('admin', { selector: '.admin__user-name' }))
    expect(await screen.findByText('You cannot change your own access here.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disable account' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Assign role' })).not.toBeInTheDocument()
  })

  it('shows the configure action when no BTP hierarchy exists', async () => {
    vi.spyOn(api.admin, 'btpHierarchy').mockResolvedValue({ accounts: [], environments: [] })
    renderAs('admin@example.com', ['administrator'], '/admin')
    await userEvent.click(await screen.findByRole('tab', { name: 'BTP scopes' }))
    expect(await screen.findByText(/No BTP hierarchy is configured/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Configure BTP scopes' })).toBeInTheDocument()
  })
})

async function expectRowGone(name: RegExp) {
  await screen.findByRole('button', { name: 'Assign' })
  await new Promise((r) => setTimeout(r, 0))
  expect(screen.queryByRole('row', { name })).not.toBeInTheDocument()
}
