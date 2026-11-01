import type { ApiErrorBody, Conversation, Message, Page } from '../types/api'

/**
 * Centralized API client. In development the Vite dev server proxies /api to FastAPI,
 * so the default base is same-origin. Set VITE_API_BASE_URL to call another origin.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const API_PREFIX = `${API_BASE}/api/v1`
const REQUEST_TIMEOUT_MS = 15_000

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'unavailable'
  | 'not_found'
  | 'conflict'
  | 'validation'
  | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly requestId: string | null

  constructor(kind: ApiErrorKind, message: string, status: number | null, requestId: string | null) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.requestId = requestId
  }

  /** Safe to retry with the same idempotency key. */
  get retryable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout' || this.kind === 'unavailable'
  }
}

const MESSAGES: Record<ApiErrorKind, string> = {
  network: 'Cannot reach the server. Check that the backend is running and try again.',
  timeout: 'The server took too long to respond. Please try again.',
  unavailable: 'The database is temporarily unavailable. Please try again shortly.',
  not_found: 'This conversation could not be found.',
  conflict: 'This message conflicts with one that was already saved.',
  validation: 'The request was not accepted. Please check your input.',
  server: 'Something went wrong on the server. Please try again.',
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 400 || status === 422) return 'validation'
  if (status === 503) return 'unavailable'
  // The dev proxy answers 502/504 when FastAPI itself is down.
  if (status === 502 || status === 504) return 'network'
  return 'server'
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ data: T; status: number }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}) },
      signal: controller.signal,
    })
  } catch {
    const kind: ApiErrorKind = controller.signal.aborted ? 'timeout' : 'network'
    throw new ApiError(kind, MESSAGES[kind], null, null)
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    const kind = kindForStatus(response.status)
    let requestId = response.headers.get('x-request-id')
    try {
      const body = (await response.json()) as Partial<ApiErrorBody>
      requestId = body.error?.request_id ?? requestId
    } catch {
      // Non-JSON error bodies (e.g. from a proxy) carry nothing we want to show.
    }
    throw new ApiError(kind, MESSAGES[kind], response.status, requestId)
  }

  return { data: (await response.json()) as T, status: response.status }
}

export const api = {
  async listConversations(limit = 100): Promise<Page<Conversation>> {
    return (await request<Page<Conversation>>(`/conversations?limit=${limit}`)).data
  },

  async getConversation(id: string): Promise<Conversation> {
    return (await request<Conversation>(`/conversations/${encodeURIComponent(id)}`)).data
  },

  async createConversation(title?: string): Promise<Conversation> {
    const body = JSON.stringify(title ? { title } : {})
    return (await request<Conversation>('/conversations', { method: 'POST', body })).data
  },

  async listMessages(conversationId: string, options: { before?: string; limit?: number } = {}): Promise<Page<Message>> {
    const params = new URLSearchParams({ limit: String(options.limit ?? 100) })
    if (options.before) params.set('before', options.before)
    const path = `/conversations/${encodeURIComponent(conversationId)}/messages?${params}`
    return (await request<Page<Message>>(path)).data
  },

  /** Idempotent: resending the same clientMessageId never creates a duplicate. */
  async sendMessage(conversationId: string, content: string, clientMessageId: string): Promise<Message> {
    const body = JSON.stringify({ content, client_message_id: clientMessageId })
    const path = `/conversations/${encodeURIComponent(conversationId)}/messages`
    return (await request<Message>(path, { method: 'POST', body })).data
  },
}
