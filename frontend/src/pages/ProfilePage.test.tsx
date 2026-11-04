import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { FakeBackend, PASSWORD } from '../test/fakeBackend'

let backend: FakeBackend

beforeEach(() => {
  backend = new FakeBackend().install()
  backend.addUser('olive@example.com', ['operator'], { name: 'Olive Operator' })
  backend.signIn('olive@example.com')
  window.history.replaceState(null, '', '/profile')
})

describe('ProfilePage', () => {
  it('shows the account and its roles and scopes', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Profile' })).toBeInTheDocument()
    expect(screen.getByText('olive@example.com')).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /Operator.*Acme EU/ })).toBeInTheDocument()
  })

  it('changes the password with client-side checks first', async () => {
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Current password'), PASSWORD)
    await userEvent.type(screen.getByLabelText('New password'), 'short')
    expect(screen.getByRole('alert')).toHaveTextContent('at least 12 characters')
    await userEvent.clear(screen.getByLabelText('New password'))
    await userEvent.type(screen.getByLabelText('New password'), 'a brand new passphrase')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'a brand new passphrase')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Password changed. 2 other sessions signed out.')
    expect(backend.users.get('olive@example.com')!.password).toBe('a brand new passphrase')
  })

  it('reports a wrong current password', async () => {
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Current password'), 'not it at all')
    await userEvent.type(screen.getByLabelText('New password'), 'a brand new passphrase')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'a brand new passphrase')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The current password is incorrect.')
  })

  it('signs out on all devices after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out on all devices' }))
    expect(await screen.findByText('You have been signed out on all devices.')).toBeInTheDocument()
    expect(backend.callsTo('POST', '/api/v1/auth/logout-all')).toHaveLength(1)
  })
})
