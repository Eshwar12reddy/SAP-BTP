import { describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './api'

function respond(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })),
  )
}

async function errorFrom(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (e) {
    if (e instanceof ApiError) return e
    throw e
  }
  throw new Error('expected the request to fail')
}

describe('api client', () => {
  it('maps a network failure to a retryable error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    const error = await errorFrom(api.listConversations())
    expect(error.kind).toBe('network')
    expect(error.retryable).toBe(true)
  })

  it.each([
    [503, 'unavailable', true],
    [502, 'network', true],
    [404, 'not_found', false],
    [409, 'conflict', false],
    [422, 'validation', false],
    [500, 'server', false],
  ])('maps HTTP %i to %s', async (status, kind, retryable) => {
    respond(status, { error: { code: 'x', message: 'raw server detail', request_id: 'abc' } })
    const error = await errorFrom(api.getConversation('id'))
    expect(error.kind).toBe(kind)
    expect(error.retryable).toBe(retryable)
    expect(error.requestId).toBe('abc')
    // Raw backend messages are never surfaced.
    expect(error.message).not.toContain('raw server detail')
  })

  it('times out slow requests', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_: string, init: RequestInit) =>
          new Promise((_, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))),
      ),
    )
    const pending = errorFrom(api.listConversations())
    await vi.advanceTimersByTimeAsync(15_000)
    expect((await pending).kind).toBe('timeout')
    vi.useRealTimers()
  })

  it('sends the idempotency key with messages', async () => {
    respond(201, { id: 'm1' })
    await api.sendMessage('c1', 'hi', 'key-1')
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/conversations/c1/messages')
    expect(JSON.parse(String(init.body))).toEqual({ content: 'hi', client_message_id: 'key-1' })
  })
})
