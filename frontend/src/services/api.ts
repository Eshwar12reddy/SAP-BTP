import {
  credentials,
  getAccessToken,
  notifySessionExpired,
  readCsrfToken,
  refreshSession,
  setAccessToken,
} from '../auth/session'
import type {
  AdminUserDetail,
  AdminUserSummary,
  AgentReply,
  ApiErrorBody,
  AuditEvent,
  Conversation,
  Grant,
  GrantCreate,
  ManageableScopes,
  Me,
  Message,
  MyScopes,
  Page,
  ReplyStatus,
  TokenResponse,
  User,
} from '../types/api'

/**
 * Centralized API client. In development the Vite dev server proxies /api to FastAPI,
 * so the default base is same-origin. Set VITE_API_BASE_URL to call another origin.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const API_PREFIX = `${API_BASE}/api/v1`
const REQUEST_TIMEOUT_MS = 15_000
// An agent reply may make two DeepSeek calls, each bounded by a 90 s server-side deadline.
export const REPLY_TIMEOUT_MS = 200_000

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'unavailable'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'validation'
  | 'rate_limited'
  | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly requestId: string | null
  readonly code: string | null

  constructor(
    kind: ApiErrorKind,
    message: string,
    status: number | null,
    requestId: string | null,
    code: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.requestId = requestId
    this.code = code
  }

  /** Safe to retry with the same idempotency key. */
  get retryable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout' || this.kind === 'unavailable'
  }
}

const MESSAGES: Record<ApiErrorKind, string> = {
  network: 'Cannot reach the server. Check that the backend is running and try again.',
  timeout: 'The server took too long to respond. Please try again.',
  unavailable: 'The service is temporarily unavailable. Please try again shortly.',
  unauthorized: 'Your session has ended. Please sign in again.',
  forbidden: 'You do not have permission to do that.',
  not_found: 'This item could not be found.',
  conflict: 'This change conflicts with existing data.',
  validation: 'The request was not accepted. Please check your input.',
  rate_limited: 'Too many attempts. Please wait and try again.',
  server: 'Something went wrong on the server. Please try again.',
}

// Our backend writes these messages for end users; anything else gets a generic text.
const SERVER_MESSAGE_STATUSES = new Set([400, 401, 403, 409, 429])

function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 429) return 'rate_limited'
  if (status === 400 || status === 422) return 'validation'
  if (status === 503) return 'unavailable'
  // The dev proxy answers 502/504 when FastAPI itself is down.
  if (status === 502 || status === 504) return 'network'
  return 'server'
}

interface RequestOptions {
  /** Attach the access token and renew it once on 401. */
  auth?: boolean
  timeoutMs?: number
}

async function send(path: string, init: RequestInit, auth: boolean, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const token = auth ? getAccessToken() : null
  try {
    return await fetch(`${API_PREFIX}${path}`, {
      ...init,
      credentials,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers as Record<string, string> | undefined),
      },
      signal: controller.signal,
    })
  } catch {
    const kind: ApiErrorKind = controller.signal.aborted ? 'timeout' : 'network'
    throw new ApiError(kind, MESSAGES[kind], null, null)
  } finally {
    clearTimeout(timer)
  }
}

/** AI-service errors written by our backend (never raw provider text), by status. */
const AI_ERROR_KINDS: Record<number, ApiErrorKind> = { 429: 'rate_limited', 502: 'server', 503: 'unavailable', 504: 'timeout' }

async function toError(response: Response): Promise<ApiError> {
  let kind = kindForStatus(response.status)
  let requestId = response.headers.get('x-request-id')
  let code: string | null = null
  let message = MESSAGES[kind]
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>
    requestId = body.error?.request_id ?? requestId
    code = body.error?.code ?? null
    if (SERVER_MESSAGE_STATUSES.has(response.status) && body.error?.message) message = body.error.message
    // A deepseek_* code means FastAPI answered: a 502/504 is the AI service, not the proxy.
    if (code?.startsWith('deepseek_') && body.error?.message) {
      kind = AI_ERROR_KINDS[response.status] ?? kind
      message = body.error.message
    }
  } catch {
    // Non-JSON error bodies (e.g. from a proxy) carry nothing we want to show.
  }
  return new ApiError(kind, message, response.status, requestId, code)
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { auth = true, timeoutMs }: RequestOptions = {},
): Promise<{ data: T; status: number }> {
  let response = await send(path, init, auth, timeoutMs)
  if (auth && response.status === 401) {
    // Renew once and retry the original request once; never loop.
    const renewed = await refreshSession()
    if (!renewed) {
      notifySessionExpired()
      throw await toError(response)
    }
    response = await send(path, init, auth, timeoutMs)
    if (response.status === 401) {
      notifySessionExpired()
      throw await toError(response)
    }
  }
  if (!response.ok) throw await toError(response)
  if (response.status === 204) return { data: undefined as T, status: 204 }
  return { data: (await response.json()) as T, status: response.status }
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })
const enc = encodeURIComponent

export const api = {
  // ---------- auth ----------

  async login(email: string, password: string): Promise<TokenResponse> {
    const { data } = await request<TokenResponse>('/auth/login', json({ email, password }), { auth: false })
    setAccessToken(data.access_token)
    return data
  },

  async logout(): Promise<void> {
    try {
      await request<void>('/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': readCsrfToken() } }, { auth: false })
    } finally {
      setAccessToken(null)
    }
  },

  async logoutAll(): Promise<void> {
    await request<void>('/auth/logout-all', { method: 'POST' })
    setAccessToken(null)
  },

  async me(): Promise<Me> {
    return (await request<Me>('/auth/me')).data
  },

  async changePassword(currentPassword: string, newPassword: string): Promise<{ other_sessions_revoked: number }> {
    const body = { current_password: currentPassword, new_password: newPassword }
    return (await request<{ other_sessions_revoked: number }>('/auth/change-password', json(body))).data
  },

  async myScopes(): Promise<MyScopes> {
    return (await request<MyScopes>('/users/me/scopes')).data
  },

  // ---------- conversations ----------

  async listConversations(limit = 100): Promise<Page<Conversation>> {
    return (await request<Page<Conversation>>(`/conversations?limit=${limit}`)).data
  },

  async getConversation(id: string): Promise<Conversation> {
    return (await request<Conversation>(`/conversations/${enc(id)}`)).data
  },

  async createConversation(context: { btp_account_id?: string; btp_environment_id?: string } = {}): Promise<Conversation> {
    return (await request<Conversation>('/conversations', json(context))).data
  },

  async listMessages(conversationId: string, options: { before?: string; limit?: number } = {}): Promise<Page<Message>> {
    const params = new URLSearchParams({ limit: String(options.limit ?? 100) })
    if (options.before) params.set('before', options.before)
    return (await request<Page<Message>>(`/conversations/${enc(conversationId)}/messages?${params}`)).data
  },

  /** Idempotent: resending the same clientMessageId never creates a duplicate. */
  async sendMessage(conversationId: string, content: string, clientMessageId: string): Promise<Message> {
    const body = { content, client_message_id: clientMessageId }
    return (await request<Message>(`/conversations/${enc(conversationId)}/messages`, json(body))).data
  },

  /**
   * Ask the agent to answer a saved user message. The backend stores at most one reply
   * per message, so repeating this returns the stored reply instead of a new AI call.
   */
  async requestReply(conversationId: string, messageId: string): Promise<AgentReply> {
    const path = `/conversations/${enc(conversationId)}/messages/${enc(messageId)}/reply`
    return (await request<AgentReply>(path, { method: 'POST' }, { timeoutMs: REPLY_TIMEOUT_MS })).data
  },

  /** Where a reply stands (completed replies included). Read-only: never triggers the AI. */
  async replyStatus(conversationId: string, messageId: string): Promise<ReplyStatus> {
    return (await request<ReplyStatus>(`/conversations/${enc(conversationId)}/messages/${enc(messageId)}/reply`)).data
  },

  // ---------- administration ----------

  admin: {
    async listUsers(search = '', offset = 0): Promise<Page<AdminUserSummary>> {
      const params = new URLSearchParams({ limit: '50', offset: String(offset) })
      if (search.trim()) params.set('search', search.trim())
      return (await request<Page<AdminUserSummary>>(`/admin/users?${params}`)).data
    },
    async getUser(id: string): Promise<AdminUserDetail> {
      return (await request<AdminUserDetail>(`/admin/users/${enc(id)}`)).data
    },
    async createUser(body: { email: string; display_name: string; password: string }): Promise<User> {
      return (await request<User>('/admin/users', json(body))).data
    },
    async setActive(id: string, active: boolean): Promise<User> {
      return (await request<User>(`/admin/users/${enc(id)}/${active ? 'enable' : 'disable'}`, { method: 'POST' })).data
    },
    async revokeSessions(id: string): Promise<{ sessions_revoked: number }> {
      return (await request<{ sessions_revoked: number }>(`/admin/users/${enc(id)}/sessions/revoke`, { method: 'POST' })).data
    },
    async assignGrant(userId: string, body: GrantCreate): Promise<Grant> {
      return (await request<Grant>(`/admin/users/${enc(userId)}/grants`, json(body))).data
    },
    async revokeGrant(grantId: string): Promise<Grant> {
      return (await request<Grant>(`/admin/grants/${enc(grantId)}/revoke`, { method: 'POST' })).data
    },
    async scopes(): Promise<ManageableScopes> {
      return (await request<ManageableScopes>('/admin/scopes')).data
    },
    async auditEvents(): Promise<Page<AuditEvent>> {
      return (await request<Page<AuditEvent>>('/admin/audit-events?limit=50')).data
    },
  },
}
