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

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled' | 'execution_unavailable'
export type ApprovalDecision = 'execute' | 'cancel' | 'other_approach' | 'explain_command'

export interface Approval {
  id: string
  operation_type: string
  operation_summary: string
  risk_level: string
  status: ApprovalStatus
  execution_available: boolean
  expires_at: string
  created_at: string
  decision: ApprovalDecision | null
  decision_at: string | null
  result_summary: string | null
}

export interface InvestigationFinding {
  finding_id: string
  rule_id: string
  resource_ref: string
  evidence_refs: string[]
  observed_at: string
  severity: 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  confidence: 'CONFIRMED' | 'LIKELY' | 'POSSIBLE' | 'UNKNOWN'
  explanation: string
  possible_causes: string[]
  next_steps: string[]
  missing_evidence: string[]
}

export interface InvestigationStatus {
  thread_id: string
  status: string
  current_step: string
  completed_steps: string[]
  pending_steps: string[]
  failure: { code: string; message: string; retryable: boolean } | null
  final_response_reference: string | null
  iteration: number
  llm_calls: number
  tool_calls: number
  investigation_plan: { investigation_type: string; target_ref: string | null; steps: { step_id: string; description: string; evidence_source: string; read_only: boolean }[]; bounded: boolean } | null
  findings: InvestigationFinding[]
  missing_evidence: string[]
  recommendations: string[]
  pending_approval: Approval | null
}

export interface ApprovalDecisionResult {
  approval: Approval
  message: string
  response: string | null
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

// ---------- BTP connections ----------

export type ConnectionValidationResult =
  | 'not_validated'
  | 'valid_configuration'
  | 'validated'
  | 'credentials_unavailable'
  | 'authentication_failed'
  | 'endpoint_unreachable'
  | 'tls_failure'
  | 'unsupported_runtime'
  | 'unsupported_authentication'
  | 'connection_disabled'
  | 'invalid_configuration'

export interface BtpConnection {
  id: string
  display_name: string
  btp_account_id: string | null
  btp_environment_id: string
  environment_name: string
  runtime_type: string
  api_endpoint: string
  authentication_method: string
  credential_configured: boolean
  enabled: boolean
  status: 'enabled' | 'disabled' | 'unsupported'
  last_validation_at: string | null
  last_validation_result: ConnectionValidationResult
  last_validation_error_category: string | null
  revision: number
  created_at: string
  updated_at: string
}

export interface AvailableEnvironment {
  id: string
  name: string
  btp_account_id: string | null
  runtime_type: string
  connection_id: string | null
  connection_name: string | null
  enabled: boolean
  status: 'available' | 'selected' | 'disabled' | 'unsupported' | 'not_configured'
  validation_result: ConnectionValidationResult | null
}

export interface ConfiguredBtpAccount {
  id: string
  external_account_id: string
  name: string
  account_type: 'global_account' | 'subaccount'
  region: string | null
  parent_account_id: string | null
  created_at: string
}

export interface ConfiguredBtpEnvironment {
  id: string
  btp_account_id: string
  name: string
  runtime_type: string
  external_runtime_id: string | null
  created_at: string
}

export interface BtpHierarchy {
  accounts: ConfiguredBtpAccount[]
  environments: ConfiguredBtpEnvironment[]
}

export interface ConnectionValidation {
  connection: BtpConnection
  result: ConnectionValidationResult
  error_category: string | null
  live_check_performed: boolean
}

// ---------- read-only Cloud Foundry inventory ----------

export interface ObservationEvidence {
  environment_id: string
  connection_id: string
  observed_at: string
  evidence_origin: 'cloud_foundry_v3'
  availability: 'available'
  source_type?: 'REGISTERED_CONFIGURATION' | 'LIVE_PROVIDER' | 'DOCUMENTED_REFERENCE'
  retrieval_status?: 'AVAILABLE' | 'NOT_CONFIGURED' | 'UNAUTHORIZED' | 'UNSUPPORTED' | 'PARTIAL' | 'FAILED' | 'NOT_YET_RETRIEVED'
}

export interface CloudFoundryOrganization {
  id: string
  display_name: string
  created_at: string | null
  updated_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundrySpace {
  id: string
  display_name: string
  organization_id: string | null
  created_at: string | null
  updated_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundryApplication {
  id: string
  display_name: string
  lifecycle_state: string | null
  lifecycle: string | null
  space_id: string | null
  created_at: string | null
  updated_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundryServiceInstance {
  id: string
  display_name: string
  space_id: string | null
  created_at: string | null
  updated_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundryProcess {
  id: string
  process_type: string
  state: string | null
  instances: number | null
  application_id: string | null
  created_at: string | null
  updated_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundryProcessStat {
  process_type: string | null
  index: number | null
  state: string | null
  observed_at: string | null
  cpu: number | null
  memory_bytes: number | null
  disk_bytes: number | null
  uptime_seconds: number | null
  evidence: ObservationEvidence
}

export interface CloudFoundryProcessStats {
  process_id: string
  items: CloudFoundryProcessStat[]
  evidence: ObservationEvidence
}

export interface CloudFoundryServiceBinding {
  id: string
  binding_type: string
  application_id: string | null
  service_instance_id: string | null
  created_at: string | null
  updated_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundryApplicationEvent {
  id: string
  event_type: string | null
  application_id: string | null
  application_name: string | null
  process_type: string | null
  current_state: string | null
  previous_state: string | null
  instance_count: number | null
  created_at: string | null
  evidence: ObservationEvidence
}

export interface CloudFoundryServiceOffering {
  id: string
  display_name: string
  description: string | null
  available: boolean | null
  service_broker_id: string | null
  entitlement_status: 'AVAILABLE_IN_PROVIDER_CATALOG' | 'ENTITLED_IN_SAP_BTP' | 'UNKNOWN_ENTITLEMENT'
  evidence: ObservationEvidence
}

export interface CloudFoundryServicePlan {
  id: string
  display_name: string
  description: string | null
  available: boolean | null
  service_offering_id: string | null
  service_broker_id: string | null
  entitlement_status: 'AVAILABLE_IN_PROVIDER_CATALOG' | 'ENTITLED_IN_SAP_BTP' | 'UNKNOWN_ENTITLEMENT'
  evidence: ObservationEvidence
}

export interface QuotaValue {
  value: number | null
  state: 'value' | 'unlimited' | 'unknown'
}

export interface CloudFoundryQuota {
  id: string
  quota_type: 'organization' | 'space'
  display_name: string
  organization_ids: string[]
  space_ids: string[]
  total_memory_mb: QuotaValue
  total_instances: QuotaValue
  total_service_instances: QuotaValue
  total_routes: QuotaValue
  paid_services_allowed: boolean | null
  evidence: ObservationEvidence
}

export interface InventoryStatus {
  resource_type: 'destination' | 'entitlement'
  source_type: string
  retrieval_status: string
  observed_at: string
  environment_id: string | null
  reason: string
}

export type DiagnosisConfidence = 'CONFIRMED' | 'LIKELY' | 'POSSIBLE' | 'UNKNOWN'
export type DiagnosisSeverity = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface DiagnosisFinding {
  finding_id: string
  resource_id: string
  rule_id: string
  severity: DiagnosisSeverity
  observation: string
  evidence_references: string[]
  timestamp: string
  confidence: DiagnosisConfidence
  possible_causes: string[]
  recommended_next_steps: string[]
  resource_type?: string
  environment_id?: string | null
  source_type?: string
  calculation_details?: Record<string, unknown> | null
  missing_evidence?: string[]
  data_freshness?: string
  diagnostic_status?: string
}

export interface ApplicationRuntimeSummary {
  application_id: string
  display_name: string
  state: string | null
  lifecycle: string | null
  space_id: string | null
  requested_instances: number | null
  observed_instances: number | null
  running_instances: number | null
  process_count: number | null
  event_count: number | null
  service_instance_count: number | null
  service_binding_count: number | null
  memory_used_bytes?: number | null
  memory_limit_bytes?: number | null
  memory_utilization_percent?: number | null
  cpu_measurements_available?: boolean
  evidence_freshness?: string
}

export interface ProviderFailure {
  source: 'cloud_foundry_v3'
  operation: string
  error_code: string
  observed_at: string
}

export interface ApplicationDiagnosis {
  application_id: string
  environment_id: string
  generated_at: string
  summary: ApplicationRuntimeSummary
  findings: DiagnosisFinding[]
  evidence: { reference_id: string; source: 'cloud_foundry_v3'; resource_id: string; observed_at: string; summary: string }[]
  provider_failures: ProviderFailure[]
  missing_information: string[]
  root_cause_established: boolean
  root_cause_statement: string
  ai_explanation: string | null
  diagnostic_status?: string
  source_type?: string
  evidence_freshness?: string
}

export interface HealthOverview {
  environment_id: string
  generated_at: string
  diagnostic_status: string
  applications_checked: number
  findings: DiagnosisFinding[]
  evidence: { reference_id: string; source: 'cloud_foundry_v3'; resource_id: string; observed_at: string; summary: string }[]
  provider_failures: ProviderFailure[]
  missing_information: string[]
  evidence_freshness: string
}

// ---------- deterministic SAP BTP service recommendations ----------

export type RecommendationStatus = 'SUPPORTED' | 'UNSUPPORTED' | 'UNKNOWN' | 'NOT_REQUESTED' | 'NOT_CONFIGURED' | 'NOT_CHECKED'

export interface ServiceRequirementAnalysis {
  requirements: {
    business_objective: string | null
    application_type: string | null
    required_capabilities: string[]
    runtime_preference: 'cloudfoundry' | 'kyma' | 'cloudfoundry_or_kyma' | 'unknown'
    region: string | null
    existing_services: string[]
    unknown_requirements: string[]
    explicit_assumptions: string[]
  }
  extracted_capabilities: string[]
  missing_critical_information: string[]
  assumptions: string[]
  analysis_status: 'READY' | 'NEEDS_CLARIFICATION'
  source: 'USER_MESSAGE' | 'AUTHORIZED_CONTEXT'
}

export interface RecommendationEvidence {
  source_type: 'DOCUMENTATION' | 'LIVE_PROVIDER' | 'CALCULATION' | 'USER_PROVIDED'
  reference: string
  observed_at: string | null
  verification_status: 'VERIFIED' | 'NOT_VERIFIED' | 'PARTIAL'
}

export interface ServiceRecommendation {
  recommendation_id: string
  service_name: string
  verified_identifier: string | null
  related_user_requirements: string[]
  recommended_capability: string
  suggested_plan: string | null
  suitability_explanation: string
  eligibility_status: RecommendationStatus
  runtime_status: RecommendationStatus
  region_status: RecommendationStatus
  entitlement_status: 'NOT_VERIFIED' | 'AVAILABLE_IN_PROVIDER_CATALOG' | 'UNKNOWN_ENTITLEMENT' | 'EXISTING_SERVICE_NOT_VERIFIED'
  quota_status: 'NOT_CHECKED' | 'UNKNOWN' | 'SUFFICIENT' | 'INSUFFICIENT'
  prerequisites: string[]
  relevant_alternatives: string[]
  tradeoffs: string[]
  evidence_references: RecommendationEvidence[]
  suitability_confidence: 'HIGH' | 'MEDIUM' | 'LOW'
  availability_confidence: 'HIGH' | 'MEDIUM' | 'LOW' | 'NOT_ASSESSED'
  assumptions: string[]
  missing_information: string[]
  source_verification_time: string
}

export interface ServiceRecommendationResponse {
  analysis: ServiceRequirementAnalysis
  recommendations: ServiceRecommendation[]
  no_suitable_options: boolean
  partial_evidence: boolean
  catalog_version: string
  live_catalog: {
    retrieval_status: 'NOT_REQUESTED' | 'NOT_CONFIGURED' | 'AVAILABLE' | 'PARTIAL' | 'UNAUTHORIZED'
    environment_id: string | null
    observed_at: string | null
    provider: string | null
    notes: string[]
  }
  generated_at: string
}

export interface ServiceComparisonResponse {
  rows: Array<{
    service_key: string
    service_name: string
    capability_fit: string
    meaningful_difference: string
    runtime_status: RecommendationStatus
    region_status: RecommendationStatus
    entitlement_status: string
    prerequisites: string[]
    evidence_references: RecommendationEvidence[]
  }>
  criteria: string[]
  missing_information: string[]
  generated_at: string
}

// ---------- structured SAP BTP architecture proposals ----------

export interface ArchitectureRequirementsAnalysis {
  requirements: {
    project_name: string | null
    business_objective: string | null
    application_type: string | null
    user_count: number | null
    existing_sap_systems: string[]
    external_systems: string[]
    integration_requirements: string[]
    database_requirements: string[]
    runtime_preference: 'cloudfoundry' | 'kyma' | 'abap' | 'cloudfoundry_or_kyma' | 'unknown'
    authentication_requirements: string[]
    authorization_requirements: string[]
    preferred_region: string | null
    availability_objectives: string | null
    recovery_objectives: string | null
    expected_traffic: string | null
    missing_information: string[]
  }
  capability_keys: string[]
  missing_critical_information: string[]
  clarification_questions: string[]
  analysis_status: 'READY' | 'NEEDS_CLARIFICATION'
}

export interface ArchitectureProposal {
  proposal_id: string
  revision: number
  title: string
  overview: string
  requirements: ArchitectureRequirementsAnalysis['requirements']
  runtime_options: Array<{ runtime_id: string; name: string; suitability: string; rationale: string; prerequisites: string[]; operational_considerations: string[]; restrictions: string[]; evidence_references: string[]; verification_status: string }>
  components: Array<{ component_id: string; name: string; component_type: string; purpose: string; related_service_key: string | null; environment_id: string | null; dependencies: string[]; evidence_references: string[]; verification_status: string }>
  connections: Array<{ connection_id: string; source_component_id: string; target_component_id: string; purpose: string; communication_pattern: string; security_consideration: string; verification_status: string }>
  data_flows: Array<{ flow_id: string; source_component_id: string; target_component_id: string; data_description: string; direction: string; consistency: string; data_protection: string }>
  environments: Array<{ environment_id: string; name: string; stage: string; runtime_id: string; purpose: string; account_structure: string; status: string; evidence_references: string[] }>
  security_controls: Array<{ control_id: string; control: string; applies_to_component_ids: string[]; recommendation: string; implementation_status: string; evidence_references: string[] }>
  risks: Array<{ risk_id: string; risk: string; severity: string; mitigation: string; evidence_references: string[] }>
  assumptions: Array<{ assumption_id: string; statement: string; impact: string; requires_confirmation: boolean }>
  alternatives: Array<{ alternative_id: string; name: string; change_from_primary: string; benefits: string[]; drawbacks: string[]; prerequisites: string[]; reliability_security_implications: string[]; evidence_references: string[] }>
  cost_considerations: string[]
  evidence: Array<{ evidence_id: string; title: string; url: string; evidence_type: string; verification_status: string; observed_at: string | null; supports: string[] }>
  diagram: { format: 'MERMAID_FLOWCHART'; source: 'SERVER_GENERATED'; mermaid: string }
  validation: { valid: boolean; findings: Array<{ rule_id: string; severity: string; message: string; related_ids: string[] }>; validated_at: string }
  generated_at: string
  account_id: string | null
  environment_id: string | null
}

export interface ArchitectureComparisonResponse {
  primary_proposal_id: string
  compared_alternatives: ArchitectureProposal['alternatives']
  criteria: string[]
  missing_information: string[]
  generated_at: string
}

// ---------- deterministic SAP BTP sizing ----------

export interface SizingRequirementsAnalysis {
  requirements: Record<string, unknown>
  missing_inputs: string[]
  clarification_questions: string[]
  explicit_inputs: string[]
  analysis_status: 'READY' | 'NEEDS_CLARIFICATION'
}

export interface SizingCalculation {
  calculation_id: string
  resource_reference: string
  calculation_type: string
  input_values: Record<string, string | number | null>
  input_units: Record<string, string>
  input_provenance: Record<string, string>
  formula: { formula_id: string; formula_version: string; expression: string; description: string }
  intermediate_calculations: Array<{ name: string; value: string | number | null; unit: string; provenance: string }>
  final_value: string | number | null
  result_unit: string
  assumptions: string[]
  safety_margin_percent: string | number
  additional_capacity: string | number | null
  evidence_references: string[]
  observation_time: string | null
  verification_status: string
  warnings: string[]
  missing_inputs: string[]
  scenario_id: string
}

export interface SizingQuotaImpact {
  dimension: string
  quota_state: string
  quota_limit: string | number | null
  quota_unit: string
  existing_usage: string | number | null
  additional_allocation: string | number | null
  projected_usage: string | number | null
  remaining_capacity: string | number | null
  projected_utilization_percent: string | number | null
  compatible: boolean | null
  evidence_references: string[]
  observation_time: string | null
  warnings: string[]
}

export interface SizingResult {
  result_id: string
  scenario_id: string
  formula_version: string
  runtime_type: string
  application_name: string | null
  architecture_proposal_id: string | null
  architecture_proposal_revision: number | null
  calculations: SizingCalculation[]
  quota_impacts: SizingQuotaImpact[]
  assumptions: Array<{ assumption_id: string; statement: string; provenance: string; requires_confirmation: boolean }>
  warnings: string[]
  missing_inputs: string[]
  status: string
  generated_at: string
}

export interface SizingComparisonResponse {
  comparison_id: string
  formula_version: string
  results: SizingResult[]
  compared_dimensions: string[]
  generated_at: string
}

// ---------- static Phase 24 infrastructure plans ----------

export interface InfrastructureArtifact {
  filename: string
  media_type: string
  content: string
  sha256: string
  size_bytes: number
  validation_status: 'VALID' | 'PARTIAL' | 'INVALID' | 'UNSUPPORTED' | 'INCOMPLETE'
}

export interface InfrastructurePlan {
  plan_id: string
  owner_user_id: string
  revision: number
  architecture_proposal_id: string
  architecture_proposal_revision: number
  sizing_result_id: string | null
  sizing_formula_version: string | null
  target_runtime: string
  target_environment_id: string | null
  output_format: 'cloudfoundry' | 'mta' | 'terraform'
  resources: Array<{ resource_id: string; source_component_id: string | null; resource_type: string; target_runtime: string; proposed: boolean; observed: boolean; configuration: Record<string, unknown>; evidence_references: string[] }>
  dependencies: Array<{ dependency_id: string; source_resource_id: string; target_resource_id: string; relationship: string; required: boolean; evidence_references: string[] }>
  assumptions: string[]
  missing_prerequisites: string[]
  unsupported_components: string[]
  validations: Array<{ rule_id: string; severity: 'INFO' | 'WARNING' | 'ERROR'; message: string; related_ids: string[] }>
  validation_status: 'VALID' | 'PARTIAL' | 'INVALID' | 'UNSUPPORTED' | 'INCOMPLETE'
  generation_timestamp: string
  generator_version: string
  evidence: Array<{ reference: string; source_type: string; verification_status: string; supports: string[] }>
  artifacts: InfrastructureArtifact[]
  impact: { proposed_resources: string[]; existing_resources_referenced: string[]; potentially_required_resources: string[]; configuration_changes: string[]; associated_environments: string[]; quota_impact: string[]; prerequisites: string[]; estimated_capacity: string[]; risks: string[]; missing_information: string[]; unsupported_operations: string[] }
  review_report: string
}

export interface InfrastructurePlanRequirementsAnalysis {
  requirements: Record<string, unknown>
  required_inputs: string[]
  missing_prerequisites: string[]
  supported: boolean
  analysis_status: 'READY' | 'NEEDS_INPUT' | 'UNSUPPORTED'
}

export interface InfrastructurePlanCompareResponse {
  plan_id: string
  left_revision: number
  right_revision: number
  changed_files: string[]
  added_resources: string[]
  removed_resources: string[]
  changed_fields: string[]
  generated_at: string
}

// ---------- knowledge management and retrieval ----------

export type KnowledgeScope = 'global' | 'btp_account' | 'btp_environment'
export type KnowledgeSourceStatus = 'active' | 'removed'
export type KnowledgeValidationStatus = 'pending' | 'validated' | 'rejected'

export interface KnowledgeVersion {
  id: string
  source_id: string
  version_number: number
  original_filename: string
  media_type: 'application/pdf' | 'text/plain' | 'text/markdown'
  size_bytes: number
  sha256: string
  uploaded_by: string
  validation_status: KnowledgeValidationStatus
  validated_at: string | null
  change_note: string | null
  created_at: string
  is_active: boolean
}

export interface KnowledgeSource {
  id: string
  title: string
  description: string | null
  tags: string[]
  scope_type: KnowledgeScope
  btp_account_id: string | null
  btp_environment_id: string | null
  owner_id: string
  created_by: string
  status: KnowledgeSourceStatus
  revision: number
  created_at: string
  updated_at: string
  active_version: KnowledgeVersion
}

export interface KnowledgeIndexResult {
  status: 'published' | 'failed' | 'unavailable'
  generation_id: string | null
  parent_count: number
  child_count: number
  failure_code: string | null
}

export interface KnowledgeCitation {
  citation_id: string
  source_id: string
  source_title: string
  source_version_id: string
  version_number: number
  page_start: number | null
  page_end: number | null
  section_heading: string | null
  parent_id: string
  child_id: string
  download_url: string
}

export interface KnowledgeEvidence {
  citation: KnowledgeCitation
  excerpt: string
  parent_context: string | null
  lexical_rank: number | null
  vector_rank: number | null
  fused_score: number
}

export interface KnowledgeSearchResponse {
  query: string
  retrieval_mode: 'hybrid' | 'lexical' | 'lexical_fallback' | 'lexical_no_match'
  semantic_status: string
  reranking_status: string
  indexing_status: string
  answer_mode: 'extractive_only'
  items: KnowledgeEvidence[]
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
