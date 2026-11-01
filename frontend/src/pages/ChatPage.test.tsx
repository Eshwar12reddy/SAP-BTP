import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { FakeBackend } from '../test/fakeBackend'
import { ChatPage, SAVED_NOTICE } from './ChatPage'

let backend: FakeBackend

beforeEach(() => {
  backend = new FakeBackend().install()
})

function messagePosts() {
  return backend.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/messages'))
}

describe('ChatPage', () => {
  it('renders the conversation list, most recent first', async () => {
    backend.addConversation('Older topic')
    backend.addConversation('Newer topic')
    render(<ChatPage />)

    const list = await screen.findByRole('navigation')
    const items = await within(list).findAllByRole('button')
    expect(items.map((b) => b.textContent)).toEqual(['Newer topic', 'Older topic'])
  })

  it('shows a loading state, then an empty state', async () => {
    render(<ChatPage />)
    expect(screen.getByText('Loading conversations…')).toBeInTheDocument()
    expect(await screen.findByText('No conversations yet.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Start a conversation' })).toBeInTheDocument()
  })

  it('shows an error with retry when conversations cannot be loaded', async () => {
    backend.failNext('GET /api/v1/conversations', 'network')
    backend.addConversation('Recovered')
    render(<ChatPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the server')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: 'Recovered' })).toBeInTheDocument()
  })

  it('renders saved message history, preserving line breaks', async () => {
    const conversation = backend.addConversation('Design review')
    backend.addMessage(conversation.id, 'line one\nline two')
    render(<ChatPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'Design review' }))
    const history = await screen.findByRole('list', { name: 'Message history' })
    const body = within(history).getByText((_, el) => el?.textContent === 'line one\nline two' && el.className === 'message__body')
    expect(body).toBeInTheDocument()
    expect(window.location.pathname).toBe(`/c/${conversation.id}`)
  })

  it('creates a conversation on the first message from New chat and persists the message', async () => {
    render(<ChatPage />)
    await screen.findByText('No conversations yet.')

    await userEvent.click(screen.getByRole('button', { name: 'New chat' }))
    await userEvent.type(screen.getByLabelText('Message'), 'Check the subaccount quotas')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(SAVED_NOTICE)).toBeInTheDocument()
    const history = screen.getByRole('list', { name: 'Message history' })
    expect(within(history).getByText('Check the subaccount quotas')).toBeInTheDocument()
    // The first message also names the conversation in the sidebar.
    expect(within(screen.getByRole('navigation')).getByText('Check the subaccount quotas')).toBeInTheDocument()
    expect(backend.conversations).toHaveLength(1)
    expect(backend.messages).toHaveLength(1)
    expect(window.location.pathname).toBe(`/c/${backend.conversations[0].id}`)
    expect(screen.getByLabelText('Message')).toHaveValue('')
    // The notice is UI only; no assistant message is stored or shown.
    expect(backend.messages.every((m) => m.role === 'user')).toBe(true)
  })

  it('sends on Enter and inserts a newline on Shift+Enter', async () => {
    const conversation = backend.addConversation('Keys')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    render(<ChatPage />)

    const input = await screen.findByLabelText('Message')
    await userEvent.type(input, 'first{Shift>}{Enter}{/Shift}second')
    expect(input).toHaveValue('first\nsecond')
    expect(messagePosts()).toHaveLength(0)

    await userEvent.type(input, '{Enter}')
    await screen.findByText(SAVED_NOTICE)
    expect(messagePosts()).toHaveLength(1)
    expect(messagePosts()[0].body).toMatchObject({ content: 'first\nsecond' })
  })

  it('keeps unsent text after a failure and retries with the same client_message_id', async () => {
    const conversation = backend.addConversation('Flaky network')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    backend.failNext(`POST /api/v1/conversations/${conversation.id}/messages`, 503)
    render(<ChatPage />)

    const input = await screen.findByLabelText('Message')
    await userEvent.type(input, 'important note')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Message not saved')
    expect(input).toHaveValue('important note')
    expect(screen.queryByText(SAVED_NOTICE)).not.toBeInTheDocument()
    expect(backend.messages).toHaveLength(0)

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(SAVED_NOTICE)).toBeInTheDocument()

    const [first, second] = messagePosts()
    expect((first.body as { client_message_id: string }).client_message_id).toBe(
      (second.body as { client_message_id: string }).client_message_id,
    )
    expect(backend.messages).toHaveLength(1)
  })

  it('disables sending while a request is pending', async () => {
    const conversation = backend.addConversation('Pending')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    const realFetch = globalThis.fetch
    globalThis.fetch = async (...args: Parameters<typeof fetch>) => {
      if (String(args[0]).endsWith('/messages') && args[1]?.method === 'POST') await gate
      return realFetch(...args)
    }
    render(<ChatPage />)

    const input = await screen.findByLabelText('Message')
    await userEvent.type(input, 'hello')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    const saving = await screen.findByRole('button', { name: 'Saving…' })
    expect(saving).toBeDisabled()
    await userEvent.type(input, '{Enter}')
    release()
    await screen.findByText(SAVED_NOTICE)
    expect(messagePosts()).toHaveLength(1)
  })

  it('shows a not-found state for an unknown conversation URL', async () => {
    window.history.replaceState(null, '', '/c/00000000-0000-4000-8000-000000000000')
    render(<ChatPage />)
    expect(await screen.findByText('This conversation could not be found.')).toBeInTheDocument()
  })
})
