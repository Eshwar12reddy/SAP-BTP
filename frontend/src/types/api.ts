/** Shapes returned by the FastAPI backend (see backend/app/schemas). */

export interface Conversation {
  id: string
  title: string
  btp_account_id: string | null
  btp_environment_id: string | null
  created_at: string
  updated_at: string
}

export type MessageRole = 'user' | 'assistant' | 'system'

export interface Message {
  id: string
  conversation_id: string
  role: MessageRole
  content: string
  client_message_id: string | null
  /** Assistant replies: the user message they answer. */
  reply_to_message_id?: string | null
  /** User messages: the agent-reply attempt; null if a reply was never requested. */
  reply_status?: 'generating' | 'failed' | 'completed' | null
  created_at: string
}

// ---------- agent replies (POST /conversations/{id}/messages/{message_id}/reply) ----------

export type SourceType = 'model_knowledge' | 'stored_metadata' | 'documentation' | 'calculation' | 'live_btp'

export interface ToolResultSummary {
  tool_name: string
  status: 'success' | 'unavailable' | 'denied' | 'invalid_input' | 'failed'
  source_type: SourceType | null
  live_check_performed: boolean
  error_code: string | null
  timestamp: string
}

export interface AgentReply {
  message: Message
  route: string
  routing_method: 'rule' | 'model'
  outcome: string
  sources: SourceType[]
  live_check_performed: boolean
  tool_results: ToolResultSummary[]
  llm_calls: number
  model: string | null
}

/** GET /conversations/{id}/messages/{message_id}/reply: read-only, never starts an AI call. */
export interface ReplyStatus {
  status: 'not_requested' | 'generating' | 'failed' | 'completed'
  reply: AgentReply | null
}

export interface Page<T> {
  items: T[]
  has_more: boolean
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id: string | null
  }
}

// ---------- auth ----------

export type RoleName = 'viewer' | 'operator' | 'approver' | 'administrator' | 'knowledge_reader' | 'knowledge_manager'
export type ScopeType = 'platform' | 'global_account' | 'subaccount' | 'environment'

export interface User {
  id: string
  email: string
  display_name: string
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export interface Grant {
  id: string
  role: RoleName
  scope_type: ScopeType
  scope_id: string | null
  scope_name: string | null
  include_descendants: boolean
  expires_at: string | null
  created_at?: string | null
  revoked_at?: string | null
}

export interface Me {
  user: User
  roles: RoleName[]
  permissions: string[]
  grants: Grant[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_at: string
  user: User
}

export interface ScopeAccount {
  id: string
  name: string
  account_type: string
}

export interface ScopeEnvironment {
  id: string
  name: string
  runtime_type: string
  btp_account_id: string | null
}

export interface MyScopes {
  grants: Grant[]
  btp_read: { accounts: ScopeAccount[]; environments: ScopeEnvironment[] }
}

// ---------- admin ----------

export interface AdminUserSummary extends User {
  roles: RoleName[]
}

export interface AdminUserDetail {
  user: User
  grants: Grant[]
  active_sessions: number
  manageable_account: boolean
}

export interface ManageableScopes {
  platform: boolean
  accounts: { id: string; name: string; account_type: string; parent_account_id: string | null }[]
  environments: { id: string; name: string; runtime_type: string; btp_account_id: string }[]
}

export interface GrantCreate {
  role: RoleName
  scope_type: ScopeType
  scope_id: string | null
  include_descendants: boolean
  expires_at: string | null
}

export interface AuditEvent {
  id: string
  created_at: string
  event_type: string
  outcome: 'success' | 'failure' | 'denied'
  actor_user_id: string | null
  target_user_id: string | null
  scope_type: string | null
  btp_account_id: string | null
  btp_environment_id: string | null
  request_id: string | null
  details: Record<string, unknown>
}
