import { vi } from 'vitest'
import type { AdminUserDetail, AgentReply, Conversation, Grant, Me, Message, MyScopes, RoleName, User } from '../types/api'

export const MODEL_SOURCE = 'Source: general AI model knowledge (DeepSeek). No live SAP BTP system was checked.'

type Failure = 'network' | number

interface Call {
  method: string
  path: string
  body: unknown
  authorization: string | null
}

interface FakeUser {
  user: User
  password: string
  roles: RoleName[]
  permissions: string[]
  grants: Grant[]
}

const PERMISSIONS: Record<RoleName, string[]> = {
  viewer: ['chat:use', 'btp:read', 'knowledge:read'],
  operator: ['chat:use', 'btp:read', 'knowledge:read', 'operations:request', 'operations:execute_low_risk'],
  approver: ['chat:use', 'btp:read', 'knowledge:read', 'approvals:read', 'approvals:decide'],
  administrator: ['users:read', 'users:create', 'users:update', 'users:disable', 'grants:manage', 'sessions:revoke', 'audit:read'],
  knowledge_reader: ['knowledge:read'],
  knowledge_manager: ['knowledge:read', 'knowledge:create', 'knowledge:update', 'knowledge:remove'],
}

export const PASSWORD = 'correct horse battery staple'

/** An in-memory stand-in for the FastAPI backend, installed as global fetch. */
export class FakeBackend {
  users = new Map<string, FakeUser>()
  conversations: Conversation[] = []
  messages: Message[] = []
  calls: Call[] = []
  scopes: MyScopes['btp_read'] = {
    accounts: [{ id: 'acc-1', name: 'Acme EU', account_type: 'subaccount' }],
    environments: [{ id: 'env-1', name: 'Development', runtime_type: 'cloudfoundry', btp_account_id: 'acc-1' }],
  }
  /** Email of the user whose refresh cookie the "browser" holds, if any. */
  sessionEmail: string | null = null
  validTokens = new Set<string>()
  refreshCount = 0
  /** Status codes returned by the next refresh calls before normal behaviour resumes. */
  refreshQueue: number[] = []
  /** Text of the next agent replies (before the backend's source line). */
  replyText = 'Cloud Foundry is an application runtime.'
  /** User message ids whose reply another request is generating (POST answers 409). */
  generating = new Set<string>()
  /** Make the next reply request find a reply already in progress elsewhere. */
  replyBusyNext = false
  private failures: { match: string; failure: Failure; detail?: { code: string; message: string } }[] = []
  private clock = Date.parse('2026-11-01T10:00:00Z')
  private tokenCounter = 0

  install(): this {
    vi.stubGlobal('fetch', vi.fn(this.handle))
    document.cookie = 'btp_csrf=csrf-test-token; path=/'
    return this
  }

  addUser(email: string, roles: RoleName[] = ['viewer'], options: { active?: boolean; name?: string } = {}): FakeUser {
    const user: User = {
      id: crypto.randomUUID(),
      email,
      display_name: options.name ?? email.split('@')[0],
      is_active: options.active ?? true,
      created_at: this.tick(),
      last_login_at: null,
    }
    const grants: Grant[] = roles.map((role) => ({
      id: crypto.randomUUID(),
      role,
      scope_type: role === 'administrator' ? 'platform' : 'subaccount',
      scope_id: role === 'administrator' ? null : 'acc-1',
      scope_name: role === 'administrator' ? 'Platform' : 'Acme EU',
      include_descendants: false,
      expires_at: null,
    }))
    const record = { user, password: PASSWORD, roles, permissions: [...new Set(roles.flatMap((r) => PERMISSIONS[r]))], grants }
    this.users.set(email, record)
    return record
  }

  /** Simulate a browser that already holds a valid refresh cookie for this user. */
  signIn(email: string): this {
    this.sessionEmail = email
    return this
  }

  expireAccessTokens(): void {
    this.validTokens.clear()
  }

  failNext(match: string, failure: Failure, detail?: { code: string; message: string }): void {
    this.failures.push({ match, failure, detail })
  }

  addConversation(title: string, owner?: string): Conversation {
    const now = this.tick()
    const conversation: Conversation = {
      id: crypto.randomUUID(),
      title,
      btp_account_id: null,
      btp_environment_id: null,
      created_at: now,
      updated_at: now,
    }
    void owner
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

  /** The reply that was generating elsewhere finishes and is stored. */
  finishReply(messageId: string): Message {
    this.generating.delete(messageId)
    const question = this.messages.find((m) => m.id === messageId)!
    return this.storeReply(this.conversations.find((c) => c.id === question.conversation_id)!, question)
  }

  callsTo(method: string, path: string): Call[] {
    return this.calls.filter((c) => c.method === method && c.path.startsWith(path))
  }

  private tick(): string {
    this.clock += 1000
    return new Date(this.clock).toISOString()
  }

  private issueToken(email: string) {
    const token = `tok-${++this.tokenCounter}`
    this.validTokens.add(token)
    const record = this.users.get(email)!
    return { access_token: token, token_type: 'bearer', expires_at: this.tick(), user: record.user }
  }

  private currentUser(authorization: string | null): FakeUser | null {
    const token = authorization?.replace(/^Bearer /, '') ?? ''
    if (!this.validTokens.has(token) || !this.sessionEmail) return null
    return this.users.get(this.sessionEmail) ?? null
  }

  private handle = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(String(input), 'http://localhost')
    const method = (init.method ?? 'GET').toUpperCase()
    const body = init.body ? JSON.parse(String(init.body)) : undefined
    const headers = (init.headers ?? {}) as Record<string, string>
    const authorization = headers.Authorization ?? null
    this.calls.push({ method, path: url.pathname + url.search, body, authorization })

    const index = this.failures.findIndex((f) => `${method} ${url.pathname}`.startsWith(f.match))
    if (index >= 0) {
      const [{ failure, detail }] = this.failures.splice(index, 1)
      if (failure === 'network') throw new TypeError('Failed to fetch')
      // Like the real endpoint: a reply attempt that errored server-side is recorded.
      const replyFor = /\/messages\/([^/]+)\/reply$/.exec(url.pathname)
      const question = replyFor && method === 'POST' ? this.messages.find((m) => m.id === replyFor[1]) : undefined
      if (question && failure >= 500) question.reply_status = 'failed'
      const { code, message } = detail ?? { code: 'error', message: 'failed' }
      return json({ error: { code, message, request_id: 'req-1' } }, failure)
    }

    const path = url.pathname.replace(/^\/api\/v1/, '')

    // ---------- auth (cookie based) ----------
    if (path === '/auth/login') {
      const record = this.users.get(String(body.email).trim().toLowerCase())
      if (!record || record.password !== body.password || !record.user.is_active) {
        return error(401, 'invalid_credentials', 'Invalid email or password.')
      }
      this.sessionEmail = record.user.email
      return json(this.issueToken(record.user.email))
    }
    if (path === '/auth/refresh') {
      this.refreshCount++
      const queued = this.refreshQueue.shift()
      if (queued) return error(queued, queued === 409 ? 'refresh_in_progress' : 'session_expired', 'refresh failed')
      if (headers['X-CSRF-Token'] !== 'csrf-test-token') return error(403, 'csrf_failed', 'CSRF validation failed.')
      if (!this.sessionEmail) return error(401, 'session_expired', 'Your session has ended.')
      return json(this.issueToken(this.sessionEmail))
    }
    if (path === '/auth/logout') {
      this.sessionEmail = null
      this.validTokens.clear()
      return new Response(null, { status: 204 })
    }

    // ---------- everything else requires a bearer token ----------
    const me = this.currentUser(authorization)
    if (!me) return error(401, 'token_expired', 'The access token has expired.')
    const can = (permission: string) => me.permissions.includes(permission)

    if (path === '/auth/logout-all') {
      this.sessionEmail = null
      this.validTokens.clear()
      return new Response(null, { status: 204 })
    }
    if (path === '/auth/me') {
      const profile: Me = { user: me.user, roles: me.roles, permissions: me.permissions, grants: me.grants }
      return json(profile)
    }
    if (path === '/auth/change-password') {
      if (body.current_password !== me.password) return error(400, 'invalid_current_password', 'The current password is incorrect.')
      me.password = body.new_password
      return json({ other_sessions_revoked: 2 })
    }
    if (path === '/users/me/scopes') {
      return json({ grants: me.grants, btp_read: can('btp:read') ? this.scopes : { accounts: [], environments: [] } })
    }

    if (path.startsWith('/admin')) return this.admin(path, method, body, me)

    // ---------- conversations ----------
    if (!can('chat:use')) return error(403, 'forbidden', 'You do not have permission to perform this action.')
    const parts = path.replace(/^\//, '').split('/')
    if (parts[0] !== 'conversations') return json({}, 404)
    if (parts.length === 1 && method === 'GET') {
      const items = [...this.conversations].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      return json({ items, has_more: false })
    }
    if (parts.length === 1 && method === 'POST') {
      const conversation = this.addConversation('New conversation')
      conversation.btp_account_id = body?.btp_account_id ?? null
      conversation.btp_environment_id = body?.btp_environment_id ?? null
      return json(conversation, 201)
    }
    const conversation = this.conversations.find((c) => c.id === parts[1])
    if (!conversation) return error(404, 'not_found', 'Conversation not found.')
    if (parts.length === 2) return json(conversation)
    if (parts[4] === 'reply') return this.reply(conversation, parts[3], method)
    if (method === 'GET') {
      return json({ items: this.messages.filter((m) => m.conversation_id === conversation.id), has_more: false })
    }
    const existing = this.messages.find(
      (m) => m.conversation_id === conversation.id && m.client_message_id === body.client_message_id,
    )
    if (existing) {
      return existing.content === body.content ? json(existing, 200) : error(409, 'conflict', 'Conflict.')
    }
    const message = this.addMessage(conversation.id, body.content)
    message.client_message_id = body.client_message_id
    conversation.updated_at = message.created_at
    if (conversation.title === 'New conversation') conversation.title = body.content.split('\n')[0]
    return json(message, 201)
  }

  /**
   * Like the real endpoints: POST stores one reply per saved user message (a repeat returns
   * it; 409 while another request generates it); GET reports the state without side effects.
   */
  private reply(conversation: Conversation, messageId: string, method: string): Response {
    const question = this.messages.find(
      (m) => m.id === messageId && m.conversation_id === conversation.id && m.role === 'user',
    )
    if (!question) return error(404, 'not_found', 'Message not found.')
    const existing = this.messages.find((m) => m.reply_to_message_id === question.id)
    if (method === 'GET') {
      if (existing) return json({ status: 'completed', reply: agentReply(existing) })
      if (this.generating.has(question.id)) return json({ status: 'generating', reply: null })
      return json({ status: question.reply_status === 'failed' ? 'failed' : 'not_requested', reply: null })
    }
    if (existing) return json(agentReply(existing), 200)
    if (this.replyBusyNext) {
      this.replyBusyNext = false
      this.generating.add(question.id)
    }
    if (this.generating.has(question.id)) {
      return error(409, 'reply_in_progress', 'A reply to this message is still being generated.')
    }
    return json(agentReply(this.storeReply(conversation, question)), 201)
  }

  private storeReply(conversation: Conversation, question: Message): Message {
    const reply = this.addMessage(conversation.id, `${this.replyText}\n\n— ${MODEL_SOURCE}`, 'assistant')
    reply.client_message_id = null
    reply.reply_to_message_id = question.id
    question.reply_status = 'completed'
    conversation.updated_at = reply.created_at
    return reply
  }

  private admin(path: string, method: string, body: any, me: FakeUser): Response {
    if (!me.permissions.includes('users:read')) return error(403, 'forbidden', 'You do not have permission to perform this action.')
    const all = [...this.users.values()]
    const byId = (id: string) => all.find((u) => u.user.id === id)
    if (path === '/admin/users' && method === 'GET') {
      return json({ items: all.map((u) => ({ ...u.user, roles: u.roles })), has_more: false })
    }
    if (path === '/admin/users' && method === 'POST') {
      if (this.users.has(body.email.toLowerCase())) return error(409, 'email_taken', 'A user with this email already exists.')
      const created = this.addUser(body.email.toLowerCase(), [], { name: body.display_name })
      created.password = body.password
      return json(created.user, 201)
    }
    if (path === '/admin/scopes') {
      return json({
        platform: true,
        accounts: [{ id: 'acc-1', name: 'Acme EU', account_type: 'subaccount', parent_account_id: null }],
        environments: [{ id: 'env-1', name: 'Development', runtime_type: 'cloudfoundry', btp_account_id: 'acc-1' }],
      })
    }
    if (path === '/admin/audit-events') return json({ items: [], has_more: false })
    const grantMatch = /^\/admin\/grants\/([^/]+)\/revoke$/.exec(path)
    if (grantMatch) {
      for (const u of all) {
        const grant = u.grants.find((g) => g.id === grantMatch[1])
        if (grant) {
          u.grants = u.grants.filter((g) => g !== grant)
          u.roles = [...new Set(u.grants.map((g) => g.role))]
          return json({ ...grant, revoked_at: this.tick() })
        }
      }
      return error(404, 'not_found', 'Grant not found.')
    }
    const match = /^\/admin\/users\/([^/]+)(?:\/(.*))?$/.exec(path)
    const target = match && byId(match[1])
    if (!target) return error(404, 'not_found', 'User not found.')
    const action = match![2]
    if (target.user.id === me.user.id && action) return error(403, 'self_modification_forbidden', 'You cannot change your own access or account status here.')
    if (!action) {
      const detail: AdminUserDetail = { user: target.user, grants: target.grants, active_sessions: 1, manageable_account: true }
      return json(detail)
    }
    if (action === 'disable' || action === 'enable') {
      target.user = { ...target.user, is_active: action === 'enable' }
      return json(target.user)
    }
    if (action === 'sessions/revoke') return json({ sessions_revoked: 1 })
    if (action === 'grants') {
      const grant: Grant = {
        id: crypto.randomUUID(),
        role: body.role,
        scope_type: body.scope_type,
        scope_id: body.scope_id,
        scope_name: body.scope_id === 'env-1' ? 'Development' : 'Acme EU',
        include_descendants: body.include_descendants,
        expires_at: body.expires_at,
      }
      target.grants.push(grant)
      target.roles = [...new Set(target.grants.map((g) => g.role))]
      return json(grant, 201)
    }
    return error(404, 'not_found', 'Not found.')
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}

function agentReply(message: Message): AgentReply {
  return {
    message,
    route: 'general_knowledge',
    routing_method: 'rule',
    outcome: 'answered',
    sources: ['model_knowledge'],
    live_check_performed: false,
    tool_results: [],
    llm_calls: 1,
    model: 'deepseek-flash',
  }
}

function error(status: number, code: string, message: string): Response {
  return json({ error: { code, message, request_id: 'req-test' } }, status)
}
