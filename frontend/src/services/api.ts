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
  InvestigationStatus,
  ApprovalDecision,
  ApprovalDecisionResult,
  TokenResponse,
  User,
  AvailableEnvironment,
  BtpConnection,
  ConnectionValidation,
  CloudFoundryOrganization,
  CloudFoundrySpace,
  CloudFoundryApplication,
  CloudFoundryServiceInstance,
  CloudFoundryProcess,
  CloudFoundryProcessStats,
  CloudFoundryServiceBinding,
  CloudFoundryApplicationEvent,
  CloudFoundryServiceOffering,
  CloudFoundryServicePlan,
  CloudFoundryQuota,
  InventoryStatus,
  ApplicationDiagnosis,
  HealthOverview,
  KnowledgeIndexResult,
  KnowledgeSearchResponse,
  KnowledgeSource,
  KnowledgeVersion,
  BtpHierarchy,
  ConfiguredBtpAccount,
  ConfiguredBtpEnvironment,
  ServiceRequirementAnalysis,
  ServiceRecommendationResponse,
  ServiceComparisonResponse,
  ArchitectureRequirementsAnalysis,
  ArchitectureProposal,
  ArchitectureComparisonResponse,
  SizingRequirementsAnalysis,
  SizingResult,
  SizingComparisonResponse,
  InfrastructurePlan,
  InfrastructurePlanRequirementsAnalysis,
  InfrastructurePlanCompareResponse,
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
        ...(init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
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

  async investigationStatus(conversationId: string): Promise<InvestigationStatus> {
    return (await request<InvestigationStatus>(`/conversations/${enc(conversationId)}/investigation`)).data
  },

  async decideInvestigationApproval(
    conversationId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalDecisionResult> {
    return (
      await request<ApprovalDecisionResult>(
        `/conversations/${enc(conversationId)}/investigation/approvals/${enc(approvalId)}/decision`,
        json({ decision }),
      )
    ).data
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
    async btpHierarchy(): Promise<BtpHierarchy> {
      return (await request<BtpHierarchy>('/admin/btp/scopes')).data
    },
    async createBtpAccount(body: {
      external_account_id: string
      name: string
      account_type: 'global_account' | 'subaccount'
      region?: string
      parent_account_id?: string
    }): Promise<ConfiguredBtpAccount> {
      return (await request<ConfiguredBtpAccount>('/admin/btp/accounts', { method: 'POST', body: JSON.stringify(body) })).data
    },
    async updateBtpAccount(id: string, body: { external_account_id: string; name: string; region?: string }): Promise<ConfiguredBtpAccount> {
      return (await request<ConfiguredBtpAccount>(`/admin/btp/accounts/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) })).data
    },
    async createBtpEnvironment(accountId: string, body: { name: string; runtime_type: string; external_runtime_id?: string }): Promise<ConfiguredBtpEnvironment> {
      return (await request<ConfiguredBtpEnvironment>(`/admin/btp/accounts/${enc(accountId)}/environments`, { method: 'POST', body: JSON.stringify(body) })).data
    },
    async updateBtpEnvironment(id: string, body: { name: string; runtime_type: string; external_runtime_id?: string }): Promise<ConfiguredBtpEnvironment> {
      return (await request<ConfiguredBtpEnvironment>(`/admin/btp/environments/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) })).data
    },
  },

  // ---------- BTP connections ----------

  async listBtpConnections(): Promise<Page<BtpConnection>> {
    return (await request<Page<BtpConnection>>('/btp/connections?limit=100')).data
  },
  async availableBtpEnvironments(): Promise<Page<AvailableEnvironment>> {
    return (await request<Page<AvailableEnvironment>>('/btp/environments/available?limit=100')).data
  },
  async activeBtpEnvironment(): Promise<{ connection: BtpConnection | null }> {
    return (await request<{ connection: BtpConnection | null }>('/btp/environments/active')).data
  },
  async selectBtpEnvironment(connectionId: string): Promise<{ connection: BtpConnection }> {
    return (await request<{ connection: BtpConnection }>('/btp/environments/active', {
      method: 'PUT',
      body: JSON.stringify({ connection_id: connectionId }),
    })).data
  },
  async createBtpConnection(body: {
    display_name: string
    btp_environment_id: string
    api_endpoint: string
    credential_reference: string
    authentication_method: string
  }): Promise<BtpConnection> {
    return (await request<BtpConnection>('/btp/connections', { method: 'POST', body: JSON.stringify(body) })).data
  },
  async updateBtpConnection(id: string, body: {
    revision: number
    display_name?: string
    api_endpoint?: string
    credential_reference?: string
    authentication_method?: string
  }): Promise<BtpConnection> {
    return (await request<BtpConnection>(`/btp/connections/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) })).data
  },
  async disableBtpConnection(id: string): Promise<BtpConnection> {
    return (await request<BtpConnection>(`/btp/connections/${enc(id)}/disable`, { method: 'POST' })).data
  },
  async validateBtpConnection(id: string, live = false): Promise<ConnectionValidation> {
    return (await request<ConnectionValidation>(`/btp/connections/${enc(id)}/validate`, {
      method: 'POST',
      body: JSON.stringify({ live }),
    })).data
  },

  // ---------- read-only Cloud Foundry inventory ----------

  async listRuntimeOrganizations(): Promise<Page<CloudFoundryOrganization>> {
    return (await request<Page<CloudFoundryOrganization>>('/btp/runtime/organizations')).data
  },
  async listRuntimeSpaces(organizationId?: string): Promise<Page<CloudFoundrySpace>> {
    const query = organizationId ? `?organization_id=${enc(organizationId)}` : ''
    return (await request<Page<CloudFoundrySpace>>(`/btp/runtime/spaces${query}`)).data
  },
  async listRuntimeApplications(spaceId?: string): Promise<Page<CloudFoundryApplication>> {
    const query = spaceId ? `?space_id=${enc(spaceId)}` : ''
    return (await request<Page<CloudFoundryApplication>>(`/btp/runtime/applications${query}`)).data
  },
  async listRuntimeServiceInstances(spaceId?: string): Promise<Page<CloudFoundryServiceInstance>> {
    const query = spaceId ? `?space_id=${enc(spaceId)}` : ''
    return (await request<Page<CloudFoundryServiceInstance>>(`/btp/runtime/service-instances${query}`)).data
  },
  async listRuntimeServiceOfferings(): Promise<Page<CloudFoundryServiceOffering>> {
    return (await request<Page<CloudFoundryServiceOffering>>('/btp/runtime/service-offerings')).data
  },
  async listRuntimeServicePlans(serviceOfferingId?: string): Promise<Page<CloudFoundryServicePlan>> {
    const query = serviceOfferingId ? `?service_offering_id=${enc(serviceOfferingId)}` : ''
    return (await request<Page<CloudFoundryServicePlan>>(`/btp/runtime/service-plans${query}`)).data
  },
  async listRuntimeOrganizationQuotas(): Promise<Page<CloudFoundryQuota>> {
    return (await request<Page<CloudFoundryQuota>>('/btp/runtime/organization-quotas')).data
  },
  async listRuntimeSpaceQuotas(): Promise<Page<CloudFoundryQuota>> {
    return (await request<Page<CloudFoundryQuota>>('/btp/runtime/space-quotas')).data
  },
  async getRuntimeDestinationStatus(): Promise<InventoryStatus> {
    return (await request<InventoryStatus>('/btp/runtime/destinations')).data
  },
  async getRuntimeEntitlementStatus(): Promise<InventoryStatus> {
    return (await request<InventoryStatus>('/btp/runtime/entitlements')).data
  },
  async listRuntimeProcesses(applicationId: string): Promise<Page<CloudFoundryProcess>> {
    return (await request<Page<CloudFoundryProcess>>(`/btp/runtime/applications/${enc(applicationId)}/processes`)).data
  },
  async getRuntimeProcessStats(processId: string): Promise<CloudFoundryProcessStats> {
    return (await request<CloudFoundryProcessStats>(`/btp/runtime/processes/${enc(processId)}/stats`)).data
  },
  async listRuntimeServiceBindings(applicationId: string): Promise<Page<CloudFoundryServiceBinding>> {
    return (await request<Page<CloudFoundryServiceBinding>>(`/btp/runtime/applications/${enc(applicationId)}/service-bindings`)).data
  },
  async listRuntimeApplicationEvents(applicationId: string): Promise<Page<CloudFoundryApplicationEvent>> {
    return (await request<Page<CloudFoundryApplicationEvent>>(`/btp/runtime/applications/${enc(applicationId)}/events`)).data
  },
  async diagnoseRuntimeApplication(applicationId: string, expectedServiceInstanceIds: string[] = []): Promise<ApplicationDiagnosis> {
    const params = new URLSearchParams()
    for (const serviceId of expectedServiceInstanceIds) params.append('expected_service_instance_id', serviceId)
    const query = params.toString() ? `?${params.toString()}` : ''
    return (await request<ApplicationDiagnosis>(`/btp/runtime/applications/${enc(applicationId)}/diagnosis${query}`)).data
  },
  async getRuntimeHealthOverview(): Promise<HealthOverview> {
    return (await request<HealthOverview>('/btp/runtime/health/overview')).data
  },
  async getRuntimeApplicationHealth(applicationId: string, expectedRunning?: boolean): Promise<ApplicationDiagnosis> {
    const query = expectedRunning === undefined ? '' : `?expected_running=${expectedRunning ? 'true' : 'false'}`
    return (await request<ApplicationDiagnosis>(`/btp/runtime/applications/${enc(applicationId)}/health${query}`)).data
  },

  // ---------- deterministic SAP BTP service recommendations ----------

  async analyzeServiceRequirements(requestText: string): Promise<ServiceRequirementAnalysis> {
    return (await request<ServiceRequirementAnalysis>('/btp/service-recommendations/requirements/analyze', {
      method: 'POST', body: JSON.stringify({ request_text: requestText }),
    })).data
  },
  async recommendServices(body: { request_text?: string; include_live_evidence?: boolean }): Promise<ServiceRecommendationResponse> {
    return (await request<ServiceRecommendationResponse>('/btp/service-recommendations/recommend', {
      method: 'POST', body: JSON.stringify(body),
    })).data
  },
  async compareServices(serviceKeys: string[], requestText?: string): Promise<ServiceComparisonResponse> {
    return (await request<ServiceComparisonResponse>('/btp/service-recommendations/compare', {
      method: 'POST', body: JSON.stringify({ service_keys: serviceKeys, request_text: requestText }),
    })).data
  },

  // ---------- structured SAP BTP architecture advisor ----------

  async analyzeArchitectureRequirements(requestText: string): Promise<ArchitectureRequirementsAnalysis> {
    return (await request<ArchitectureRequirementsAnalysis>('/btp/architecture-advisor/requirements/analyze', { method: 'POST', body: JSON.stringify({ request_text: requestText }) })).data
  },
  async createArchitectureProposal(body: { request_text?: string; include_live_evidence?: boolean; account_id?: string; environment_id?: string }): Promise<ArchitectureProposal> {
    return (await request<ArchitectureProposal>('/btp/architecture-advisor/proposals', { method: 'POST', body: JSON.stringify(body) })).data
  },
  async reviseArchitectureProposal(id: string, body: { request_text?: string; include_live_evidence?: boolean }): Promise<ArchitectureProposal> {
    return (await request<ArchitectureProposal>(`/btp/architecture-advisor/proposals/${enc(id)}/revisions`, { method: 'POST', body: JSON.stringify(body) })).data
  },
  async getArchitectureProposal(id: string): Promise<ArchitectureProposal> {
    return (await request<ArchitectureProposal>(`/btp/architecture-advisor/proposals/${enc(id)}`)).data
  },
  async listArchitectureProposals(): Promise<Page<ArchitectureProposal>> {
    return (await request<Page<ArchitectureProposal>>('/btp/architecture-advisor/proposals')).data
  },
  async validateArchitectureProposal(proposal: ArchitectureProposal): Promise<ArchitectureProposal> {
    return (await request<ArchitectureProposal>('/btp/architecture-advisor/validate', { method: 'POST', body: JSON.stringify(proposal) })).data
  },
  async compareArchitectureProposal(proposal: ArchitectureProposal, alternativeIds: string[] = []): Promise<ArchitectureComparisonResponse> {
    return (await request<ArchitectureComparisonResponse>('/btp/architecture-advisor/compare', { method: 'POST', body: JSON.stringify({ proposal, alternative_ids: alternativeIds }) })).data
  },

  // ---------- deterministic SAP BTP sizing ----------

  async analyzeSizingRequirements(requestText: string): Promise<SizingRequirementsAnalysis> {
    return (await request<SizingRequirementsAnalysis>('/btp/sizing/requirements/analyze', { method: 'POST', body: JSON.stringify({ request_text: requestText }) })).data
  },
  async calculateSizing(body: { request_text?: string; requirements?: Record<string, unknown>; scenario_id?: string; persist?: boolean }): Promise<SizingResult> {
    return (await request<SizingResult>('/btp/sizing/calculate', { method: 'POST', body: JSON.stringify(body) })).data
  },
  async calculateSizingForArchitecture(proposalId: string, body: { request_text?: string; requirements?: Record<string, unknown>; scenario_id?: string; persist?: boolean }): Promise<SizingResult> {
    return (await request<SizingResult>(`/btp/sizing/architecture/${enc(proposalId)}/calculate`, { method: 'POST', body: JSON.stringify(body) })).data
  },
  async compareSizingScenarios(scenarios: Array<{ scenario_id: string; name: string; requirements: Record<string, unknown> }>): Promise<SizingComparisonResponse> {
    return (await request<SizingComparisonResponse>('/btp/sizing/compare', { method: 'POST', body: JSON.stringify({ scenarios }) })).data
  },
  async listSizingResults(): Promise<Page<SizingResult>> {
    return (await request<Page<SizingResult>>('/btp/sizing')).data
  },
  async getSizingResult(id: string): Promise<SizingResult> {
    return (await request<SizingResult>(`/btp/sizing/${enc(id)}`)).data
  },

  // ---------- static infrastructure plans (Phase 24) ----------

  async analyzeInfrastructurePlan(body: Record<string, unknown>): Promise<InfrastructurePlanRequirementsAnalysis> {
    return (await request<InfrastructurePlanRequirementsAnalysis>('/btp/infrastructure-plans/requirements/analyze', json(body))).data
  },
  async generateInfrastructurePlan(body: Record<string, unknown>): Promise<InfrastructurePlan> {
    return (await request<InfrastructurePlan>('/btp/infrastructure-plans/generate', json(body))).data
  },
  async validateInfrastructurePlan(plan: InfrastructurePlan): Promise<InfrastructurePlan> {
    return (await request<InfrastructurePlan>('/btp/infrastructure-plans/validate', json(plan))).data
  },
  async compareInfrastructurePlan(id: string, leftRevision: number, rightRevision: number): Promise<InfrastructurePlanCompareResponse> {
    return (await request<InfrastructurePlanCompareResponse>(`/btp/infrastructure-plans/${enc(id)}/compare?left_revision=${leftRevision}&right_revision=${rightRevision}`, { method: 'POST' })).data
  },
  async exportInfrastructurePlan(id: string, revision?: number): Promise<Blob> {
    const token = getAccessToken()
    const query = revision ? `?revision=${revision}` : ''
    const response = await fetch(`${API_PREFIX}/btp/infrastructure-plans/${enc(id)}/export${query}`, { credentials, headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) throw await toError(response)
    return response.blob()
  },

  // ---------- knowledge management and retrieval ----------

  async searchKnowledge(query: string, maxResults = 6): Promise<KnowledgeSearchResponse> {
    return (await request<KnowledgeSearchResponse>('/knowledge/search', json({ query, max_results: maxResults }))).data
  },
  async listKnowledgeSources(options: { query?: string; scopeType?: string } = {}): Promise<Page<KnowledgeSource>> {
    const params = new URLSearchParams({ limit: '50', offset: '0' })
    if (options.query?.trim()) params.set('q', options.query.trim())
    if (options.scopeType) params.set('scope_type', options.scopeType)
    return (await request<Page<KnowledgeSource>>(`/knowledge/sources?${params}`)).data
  },
  async getKnowledgeSource(id: string): Promise<KnowledgeSource> {
    return (await request<KnowledgeSource>(`/knowledge/sources/${enc(id)}`)).data
  },
  async createKnowledgeSource(fields: {
    title: string
    description?: string
    tags?: string[]
    scope_type: KnowledgeSource['scope_type']
    scope_id?: string
    change_note?: string
    file: File
  }): Promise<KnowledgeSource> {
    const form = new FormData()
    form.set('title', fields.title)
    form.set('scope_type', fields.scope_type)
    if (fields.description) form.set('description', fields.description)
    for (const tag of fields.tags ?? []) form.append('tags', tag)
    if (fields.scope_id) form.set('scope_id', fields.scope_id)
    if (fields.change_note) form.set('change_note', fields.change_note)
    form.set('file', fields.file)
    return (await request<KnowledgeSource>('/knowledge/sources', { method: 'POST', body: form })).data
  },
  async updateKnowledgeSource(id: string, body: { revision: number; title?: string; description?: string; tags?: string[] }): Promise<KnowledgeSource> {
    return (await request<KnowledgeSource>(`/knowledge/sources/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) })).data
  },
  async removeKnowledgeSource(id: string, reason: string): Promise<{ id: string; status: KnowledgeSource['status']; removed_at: string; removed_by: string }> {
    return (await request<{ id: string; status: KnowledgeSource['status']; removed_at: string; removed_by: string }>(`/knowledge/sources/${enc(id)}`, json({ reason }))).data
  },
  async uploadKnowledgeVersion(id: string, file: File, changeNote?: string): Promise<KnowledgeVersion> {
    const form = new FormData()
    form.set('file', file)
    if (changeNote) form.set('change_note', changeNote)
    return (await request<KnowledgeVersion>(`/knowledge/sources/${enc(id)}/versions`, { method: 'POST', body: form })).data
  },
  async listKnowledgeVersions(id: string): Promise<Page<KnowledgeVersion>> {
    return (await request<Page<KnowledgeVersion>>(`/knowledge/sources/${enc(id)}/versions?limit=50&offset=0`)).data
  },
  async indexKnowledgeSource(id: string): Promise<KnowledgeIndexResult> {
    return (await request<KnowledgeIndexResult>(`/knowledge/sources/${enc(id)}/index`, { method: 'POST' })).data
  },
  async downloadKnowledgeVersion(sourceId: string, versionId: string): Promise<Blob> {
    let response = await send(`/knowledge/sources/${enc(sourceId)}/versions/${enc(versionId)}/download`, {}, true, REQUEST_TIMEOUT_MS)
    if (response.status === 401) {
      const renewed = await refreshSession()
      if (!renewed) {
        notifySessionExpired()
        throw await toError(response)
      }
      response = await send(`/knowledge/sources/${enc(sourceId)}/versions/${enc(versionId)}/download`, {}, true, REQUEST_TIMEOUT_MS)
    }
    if (!response.ok) throw await toError(response)
    return response.blob()
  },
}
