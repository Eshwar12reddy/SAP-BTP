import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { POLL_INTERVAL_MS } from '../hooks/useAgentReply'
import { FakeBackend, MODEL_SOURCE } from '../test/fakeBackend'
import { REPLY_INCOMPLETE, REPLYING_NOTICE, STILL_GENERATING } from './ChatPage'

let backend: FakeBackend

beforeEach(() => {
  backend = new FakeBackend().install()
  // Chat behaviour, exercised as a signed-in viewer (session restored from the cookie).
  backend.addUser('viewer@example.com', ['viewer'])
  backend.signIn('viewer@example.com')
})

const ChatPage = App
const REPLY = 'Cloud Foundry is an application runtime.'
const conversationNav = () => screen.findByRole('navigation', { name: 'Conversation history' })

function messagePosts() {
  return backend.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/messages'))
}

function replyPosts() {
  return backend.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/reply'))
}

function replyGets() {
  return backend.calls.filter((c) => c.method === 'GET' && c.path.endsWith('/reply'))
}

/** Hold matching requests until ``release`` is called. */
function gateRequests(suffix: string, method = 'POST') {
  let release!: () => void
  const gate = new Promise<void>((resolve) => (release = resolve))
  const realFetch = globalThis.fetch
  globalThis.fetch = async (...args: Parameters<typeof fetch>) => {
    if (String(args[0]).endsWith(suffix) && args[1]?.method === method) await gate
    return realFetch(...args)
  }
  return () => release()
}

describe('ChatPage', () => {
  it('renders the conversation list, most recent first', async () => {
    backend.addConversation('Older topic')
    backend.addConversation('Newer topic')
    render(<ChatPage />)

    const list = await conversationNav()
    const items = await within(list).findAllByRole('button')
    expect(items.map((b) => b.textContent)).toEqual(['Newer topic', 'Older topic'])
  })

  it('shows a loading state, then an empty state', async () => {
    render(<ChatPage />)
    expect(await screen.findByText('Loading conversations…')).toBeInTheDocument()
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

  it('creates a conversation on the first message, saves it, then shows the agent reply', async () => {
    render(<ChatPage />)
    await screen.findByText('No conversations yet.')

    await userEvent.click(screen.getByRole('button', { name: 'New chat' }))
    await userEvent.type(screen.getByLabelText('Message'), 'Check the subaccount quotas')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    const history = await screen.findByRole('list', { name: 'Message history' })
    expect(await within(history).findByText(REPLY)).toBeInTheDocument()
    expect(within(history).getByText('Check the subaccount quotas')).toBeInTheDocument()
    // The backend's source line is shown separately from the answer.
    expect(within(history).getByText(MODEL_SOURCE)).toHaveClass('message__source')
    // The first message also names the conversation in the sidebar.
    expect(within(await conversationNav()).getByText('Check the subaccount quotas')).toBeInTheDocument()
    expect(backend.conversations).toHaveLength(1)
    expect(window.location.pathname).toBe(`/c/${backend.conversations[0].id}`)
    expect(screen.getByLabelText('Message')).toHaveValue('')

    // The reply was requested for the saved message, and stored by the server.
    const [question, reply] = backend.messages
    expect(backend.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(replyPosts().map((c) => c.path)).toEqual([
      `/api/v1/conversations/${question.conversation_id}/messages/${question.id}/reply`,
    ])
    expect(reply.reply_to_message_id).toBe(question.id)
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
    await screen.findByText(REPLY)
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
    expect(backend.messages).toHaveLength(0)
    expect(replyPosts()).toHaveLength(0) // an unsaved message is never answered

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(REPLY)).toBeInTheDocument()

    const [first, second] = messagePosts()
    expect((first.body as { client_message_id: string }).client_message_id).toBe(
      (second.body as { client_message_id: string }).client_message_id,
    )
    expect(backend.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
  })

  it('disables sending while a request is pending', async () => {
    const conversation = backend.addConversation('Pending')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    const release = gateRequests('/messages')
    render(<ChatPage />)

    const input = await screen.findByLabelText('Message')
    await userEvent.type(input, 'hello')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    const saving = await screen.findByRole('button', { name: 'Saving…' })
    expect(saving).toBeDisabled()
    await userEvent.type(input, '{Enter}')
    release()
    await screen.findByText(REPLY)
    expect(messagePosts()).toHaveLength(1)
  })

  it('shows progress while the reply is generated and requests it only once', async () => {
    const conversation = backend.addConversation('Waiting')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    const release = gateRequests('/reply')
    render(<ChatPage />)

    const input = await screen.findByLabelText('Message')
    await userEvent.type(input, 'What is Cloud Foundry?{Enter}')
    expect(await screen.findByText(REPLYING_NOTICE)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Replying…' })).toBeDisabled()
    await userEvent.type(input, 'again{Enter}')
    release()

    expect(await screen.findByText(REPLY)).toBeInTheDocument()
    expect(screen.queryByText(REPLYING_NOTICE)).not.toBeInTheDocument()
    expect(replyPosts()).toHaveLength(1)
    expect(messagePosts()).toHaveLength(1)
  })

  it('reports an AI failure and retries the reply for the same saved message', async () => {
    const conversation = backend.addConversation('AI outage')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    backend.failNext(`POST /api/v1/conversations/${conversation.id}/messages/`, 503, {
      code: 'deepseek_unavailable',
      message: 'The AI service is temporarily unavailable. Please try again shortly.',
    })
    render(<ChatPage />)

    await userEvent.type(await screen.findByLabelText('Message'), 'Explain SAP BTP.{Enter}')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('AI reply failed. The AI service is temporarily unavailable.')
    // The question is saved and shown; no reply was stored or invented.
    const history = screen.getByRole('list', { name: 'Message history' })
    expect(within(history).getByText('Explain SAP BTP.')).toBeInTheDocument()
    expect(backend.messages.map((m) => m.role)).toEqual(['user'])

    await userEvent.click(within(alert).getByRole('button', { name: 'Retry reply' }))
    expect(await screen.findByText(REPLY)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(messagePosts()).toHaveLength(1) // the user message was not sent again
    const [first, second] = replyPosts()
    expect(second.path).toBe(first.path)
    expect(backend.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
  })

  it('offers no retry when the reply is refused for access reasons', async () => {
    const conversation = backend.addConversation('Revoked')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    backend.failNext(`POST /api/v1/conversations/${conversation.id}/messages/`, 403, {
      code: 'btp_scope_revoked',
      message: "You no longer have access to this conversation's BTP context.",
    })
    render(<ChatPage />)

    await userEvent.type(await screen.findByLabelText('Message'), 'Show my apps{Enter}')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("You no longer have access to this conversation's BTP context.")
    expect(within(alert).queryByRole('button')).not.toBeInTheDocument()
  })

  it('reloads stored assistant replies as Markdown, never regenerating them', async () => {
    const conversation = backend.addConversation('Earlier answers')
    const question = backend.addMessage(conversation.id, 'Show my BTP accounts.')
    question.reply_status = 'completed'
    const stored = backend.addMessage(
      conversation.id,
      'Accounts you can access:\n- Acme EU (subaccount)\n\n— Source: the BTP account mapping stored in this application, limited to your access. This is not a live SAP BTP check.',
      'assistant',
    )
    stored.reply_to_message_id = question.id
    // A message from before AI replies existed: ordinary history, no reply action.
    backend.addMessage(conversation.id, 'Old note without a reply')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    render(<ChatPage />)

    const history = await screen.findByRole('list', { name: 'Message history' })
    const item = await within(history).findByText('Acme EU (subaccount)')
    expect(item.tagName).toBe('LI')
    expect(item.closest('.message')).toHaveClass('message--assistant')
    expect(within(history).getByText(/This is not a live SAP BTP check\.$/)).toHaveClass('message__source')
    expect(screen.queryByRole('button', { name: 'Retry reply' })).not.toBeInTheDocument()
    expect(screen.queryByText(STILL_GENERATING)).not.toBeInTheDocument()
    expect(replyPosts()).toHaveLength(0)
    expect(replyGets()).toHaveLength(0)
  })

  it('restores Retry reply after a refresh and reuses the saved message', async () => {
    const conversation = backend.addConversation('Failed earlier')
    const question = backend.addMessage(conversation.id, 'Explain SAP BTP.')
    question.reply_status = 'failed'
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    render(<ChatPage />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(`AI reply failed. ${REPLY_INCOMPLETE}`)
    expect(replyPosts()).toHaveLength(0) // loading never starts an AI call

    await userEvent.click(within(alert).getByRole('button', { name: 'Retry reply' }))
    expect(await screen.findByText(REPLY)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(replyPosts().map((c) => c.path)).toEqual([
      `/api/v1/conversations/${conversation.id}/messages/${question.id}/reply`,
    ])
    expect(messagePosts()).toHaveLength(0)
    expect(backend.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
  })

  it('treats 409 as still generating and picks up the stored reply without a second request', async () => {
    const conversation = backend.addConversation('Slow reply')
    window.history.replaceState(null, '', `/c/${conversation.id}`)
    backend.replyBusyNext = true
    render(<ChatPage />)

    await userEvent.type(await screen.findByLabelText('Message'), 'Design a CAP service{Enter}')
    const status = await screen.findByText(STILL_GENERATING)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument() // not shown as a failure
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled() // empty composer, not locked

    await userEvent.click(within(status.parentElement!).getByRole('button', { name: 'Check now' }))
    await waitFor(() => expect(replyGets()).toHaveLength(1))
    expect(screen.getByText(STILL_GENERATING)).toBeInTheDocument()

    backend.finishReply(backend.messages[0].id)
    await userEvent.click(screen.getByRole('button', { name: 'Check now' }))
    expect(await screen.findByText(REPLY)).toBeInTheDocument()
    expect(screen.queryByText(STILL_GENERATING)).not.toBeInTheDocument()
    expect(replyPosts()).toHaveLength(1)
    expect(backend.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
  })

  describe('status polling', () => {
    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
    afterEach(() => vi.useRealTimers())
    const tick = () => act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS))

    it('watches a reply still generating after a refresh, then shows it', async () => {
      const conversation = backend.addConversation('Generating elsewhere')
      const question = backend.addMessage(conversation.id, 'Explain Kyma.')
      question.reply_status = 'generating'
      backend.generating.add(question.id)
      window.history.replaceState(null, '', `/c/${conversation.id}`)
      render(<ChatPage />)

      expect(await screen.findByText(STILL_GENERATING)).toBeInTheDocument()
      await tick()
      await waitFor(() => expect(replyGets()).toHaveLength(1))
      backend.finishReply(question.id)
      await tick()
      expect(await screen.findByText(REPLY)).toBeInTheDocument()
      expect(replyPosts()).toHaveLength(0) // only read-only status checks
    })

    it('checks the status after a lost connection instead of failing or resending', async () => {
      const conversation = backend.addConversation('Dropped')
      window.history.replaceState(null, '', `/c/${conversation.id}`)
      backend.failNext(`POST /api/v1/conversations/${conversation.id}/messages/`, 'network')
      render(<ChatPage />)

      await userEvent.type(await screen.findByLabelText('Message'), 'Explain SAP BTP.{Enter}')
      expect(await screen.findByText(STILL_GENERATING)).toBeInTheDocument()
      await tick()
      // The request never reached the server: a plain failure with a retry.
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent(REPLY_INCOMPLETE)
      await userEvent.click(within(alert).getByRole('button', { name: 'Retry reply' }))
      expect(await screen.findByText(REPLY)).toBeInTheDocument()
      expect(messagePosts()).toHaveLength(1)
      expect(backend.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    })

    it('stops polling when the chat is closed', async () => {
      const conversation = backend.addConversation('Closed')
      const question = backend.addMessage(conversation.id, 'Explain Kyma.')
      question.reply_status = 'generating'
      backend.generating.add(question.id)
      window.history.replaceState(null, '', `/c/${conversation.id}`)
      const view = render(<ChatPage />)

      await screen.findByText(STILL_GENERATING)
      view.unmount()
      await tick()
      await tick()
      expect(replyGets()).toHaveLength(0)
    })
  })

  it('shows a not-found state for an unknown conversation URL', async () => {
    window.history.replaceState(null, '', '/c/00000000-0000-4000-8000-000000000000')
    render(<ChatPage />)
    expect(await screen.findByText('This conversation could not be found.')).toBeInTheDocument()
  })
})
