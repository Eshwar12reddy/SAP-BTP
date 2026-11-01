import { vi } from 'vitest'
import type { Conversation, Message } from '../types/api'

type Failure = 'network' | number

interface Call {
  method: string
  path: string
  body: unknown
}

/** An in-memory stand-in for the FastAPI conversation endpoints, installed as global fetch. */
export class FakeBackend {
  conversations: Conversation[] = []
  messages: Message[] = []
  calls: Call[] = []
  /** Queued failures, consumed in order by requests whose "METHOD path" starts with the key. */
  private failures: { match: string; failure: Failure }[] = []
  private clock = Date.parse('2026-11-01T10:00:00Z')

  install(): this {
    vi.stubGlobal('fetch', vi.fn(this.handle))
    return this
  }

  failNext(match: string, failure: Failure): void {
    this.failures.push({ match, failure })
  }

  addConversation(title: string): Conversation {
    const now = this.tick()
    const conversation = { id: crypto.randomUUID(), title, created_at: now, updated_at: now }
    this.conversations.push(conversation)
    return conversation
  }

  addMessage(conversationId: string, content: string, role: Message['role'] = 'user'): Message {
    const message: Message = {
      id: crypto.randomUUID(),
      conversation_id: conversationId,
      role,
      content,
      client_message_id: crypto.randomUUID(),
      created_at: this.tick(),
    }
    this.messages.push(message)
    return message
  }

  private tick(): string {
    this.clock += 1000
    return new Date(this.clock).toISOString()
  }

  private handle = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(String(input), 'http://localhost')
    const method = (init.method ?? 'GET').toUpperCase()
    const body = init.body ? JSON.parse(String(init.body)) : undefined
    this.calls.push({ method, path: url.pathname + url.search, body })

    const index = this.failures.findIndex((f) => `${method} ${url.pathname}`.startsWith(f.match))
    if (index >= 0) {
      const [{ failure }] = this.failures.splice(index, 1)
      if (failure === 'network') throw new TypeError('Failed to fetch')
      return json({ error: { code: 'error', message: 'failed', request_id: 'req-1' } }, failure)
    }

    const parts = url.pathname.replace(/^\/api\/v1\//, '').split('/')
    if (parts[0] !== 'conversations') return json({}, 404)

    if (parts.length === 1 && method === 'GET') {
      const items = [...this.conversations].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      return json({ items, has_more: false })
    }
    if (parts.length === 1 && method === 'POST') {
      return json(this.addConversation(body?.title ?? 'New conversation'), 201)
    }

    const conversation = this.conversations.find((c) => c.id === parts[1])
    if (!conversation) return json({ error: { code: 'not_found', message: 'Conversation not found.', request_id: 'r' } }, 404)
    if (parts.length === 2) return json(conversation)

    if (method === 'GET') {
      return json({ items: this.messages.filter((m) => m.conversation_id === conversation.id), has_more: false })
    }
    const existing = this.messages.find(
      (m) => m.conversation_id === conversation.id && m.client_message_id === body.client_message_id,
    )
    if (existing) {
      return existing.content === body.content ? json(existing, 200) : json({ error: { code: 'conflict' } }, 409)
    }
    const message = this.addMessage(conversation.id, body.content)
    message.client_message_id = body.client_message_id
    conversation.updated_at = message.created_at
    if (conversation.title === 'New conversation') conversation.title = body.content.split('\n')[0]
    return json(message, 201)
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}
