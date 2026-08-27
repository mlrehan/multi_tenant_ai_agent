// Mirrors the backend exactly (see the API reference gathered against
// src/iam_platform/api/v1/**/schemas.py and domain/**/entities.py). Do not
// "improve" a shape here without checking the backend first -- this file
// exists so the frontend never guesses.

export type TenantStatus = "pending" | "active" | "suspended" | "deactivated";
export type MembershipStatus = "invited" | "active" | "suspended" | "revoked";
export type Visibility = "tenant" | "department" | "team" | "restricted";
export type AssistantStatus = "draft" | "published" | "archived";
export type AccessLevel = "viewer" | "editor" | "owner";
export type ConversationStatus = "active" | "archived";
export type OverrideEffect = "allow" | "deny";
export type RiskLevel = "low" | "medium" | "high" | "critical";

// ---- Auth ----

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_in: number;
}

export interface LoginResponse {
  status: "success" | "mfa_required";
  tokens: TokenResponse | null;
  mfa_challenge_id: string | null;
}

export interface WeakPasswordViolation {
  detail: string;
  violations: string[];
}

export type UserStatus = "pending_verification" | "active" | "suspended" | "deactivated";

// ---- Identity (own account) ----

export interface MfaMethodSummary {
  id: string;
  type: "totp" | "webauthn" | "recovery_code";
  label: string | null;
  is_primary: boolean;
  verified: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface LinkedProvider {
  provider: string;
  provider_email: string | null;
  linked_at: string;
}

/** GET /v1/auth/me. Carries no secret material by construction — the backend
 * DTO has no field capable of holding a TOTP secret or password hash. */
export interface AccountProfile {
  user_id: string;
  email: string;
  status: UserStatus;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
  has_password: boolean;
  mfa_methods: MfaMethodSummary[];
  linked_providers: LinkedProvider[];
}

// ---- Platform user directory ----

export interface PlatformUser {
  id: string;
  email: string;
  status: UserStatus;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface PlatformUserPage {
  users: PlatformUser[];
  total: number;
  limit: number;
  offset: number;
}

export interface PlatformUserMembership {
  membership_id: string;
  tenant_id: string;
  tenant_slug: string;
  tenant_display_name: string;
  status: MembershipStatus;
  is_default: boolean;
  job_title: string | null;
}

export interface PlatformUserDetail {
  user: PlatformUser;
  platform_roles: string[];
  platform_permissions: string[];
  memberships: PlatformUserMembership[];
}

/** Role code -> the permission codes that role *definition* grants. Not the
 * same as a member's effective permissions: hierarchy inheritance and
 * overrides apply on top. */
export interface RolePermissionMap {
  by_role_code: Record<string, string[]>;
}

// ---- Tenancy ----

export interface Tenant {
  id: string;
  slug: string;
  display_name: string;
  status: TenantStatus;
  owner_user_id: string;
  created_at: string;
  suspended_at: string | null;
  suspended_reason: string | null;
}

export interface TenantMembership {
  membership_id: string;
  tenant_id: string;
  status: MembershipStatus;
  is_default: boolean;
}

export interface TenantMember {
  membership_id: string;
  user_id: string;
  status: MembershipStatus;
  is_default: boolean;
  department_id: string | null;
  team_id: string | null;
  job_title: string | null;
  created_at: string;
}

export interface MembershipRoleAssignment {
  role_id: string;
  granted_at: string;
}

// ---- Platform / tenant RBAC catalog ----

export interface RoleSummary {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_system: boolean;
  rank: number;
}

export interface PermissionSummary {
  code: string;
  resource: string;
  action: string;
  description: string | null;
  risk_level: RiskLevel;
  is_system: boolean;
}

export interface TenantPermissionSummary extends PermissionSummary {
  tenant_customizable: boolean;
  required_feature: string | null;
}

// ---- AI resources ----

export interface Assistant {
  id: string;
  name: string;
  description: string | null;
  visibility: Visibility;
  department_id: string | null;
  team_id: string | null;
  owner_membership_id: string;
  model_configuration_id: string;
  system_prompt: string | null;
  /** The guided brief the AI Chatbot console edits. Returned so an edit form
   *  is populated with what is stored — the failure this response already had
   *  once, when `system_prompt` was missing and every save silently
   *  overwrote the existing prompt with an empty string. */
  role_instructions: string | null;
  avoid_instructions: string | null;
  personality: Personality;
  response_length: ResponseLength;
  status: AssistantStatus;
  created_at: string;
  updated_at: string;
}

export type DocumentStatus = "processing" | "ready" | "failed";

export interface KnowledgeBaseDocument {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  /** Populated only when status === "failed". */
  failure_reason: string | null;
  /** Searchable chunks produced. `status: "ready"` with 0 chunks means the
   *  pipeline finished but found nothing to index. */
  chunk_count: number;
  created_at: string;
}

export interface DocumentChunk {
  id: string;
  chunk_index: number;
  text: string;
  token_count: number;
  /** "page 7", "row 12", or the page URL for a crawled document. */
  source_location: string | null;
}

export interface DocumentDetail {
  document: KnowledgeBaseDocument;
  /** The requested page of chunks, in document order. */
  chunks: DocumentChunk[];
  /** Total for the document, not the length of `chunks`. */
  chunk_count: number;
  /** Present for a crawled page, absent for an uploaded file. */
  source_url: string | null;
}

export type SyncStatus = "idle" | "syncing" | "ready" | "error";
export type CrawlMode = "url_list" | "site";

export interface DataSource {
  id: string;
  knowledge_base_id: string;
  urls: string[];
  mode: CrawlMode;
  sync_status: SyncStatus;
  /** Populated only when sync_status === "error". */
  failure_reason: string | null;
  pages_discovered: number;
  pages_indexed: number;
  last_synced_at: string | null;
  created_at: string;
}

export type WidgetStatus = "active" | "disabled";

export interface ChatWidget {
  id: string;
  knowledge_base_id: string;
  name: string;
  /** An identifier, not a secret -- it ships in a public script tag. Unlike a
   *  provider credential, it is safe to display and copy. */
  public_key: string;
  allowed_origins: string[];
  status: WidgetStatus;
  daily_question_limit: number;
  created_at: string;
  /** Built server-side, because only the server knows this API's public
   *  origin -- see the note in the widget card. */
  embed_snippet: string;
}

/** One model the current tenant is allowed to assign. The server returns only
 *  available configurations, so there is nothing here to filter on. */
export interface ModelConfiguration {
  id: string;
  model_name: string;
  /** The platform's monthly cap on this model, and what this tenant has spent
   *  against it. `tokens_used_this_month` is null when the counter could not
   *  be read — deliberately distinct from 0. */
  token_budget_per_month: number | null;
  tokens_used_this_month: number | null;
}

/** The platform-side view: the catalogue, plus who may use each entry. */
export interface PlatformModelConfiguration {
  id: string;
  model_name: string;
  parameters: Record<string, unknown>;
  token_budget_per_month: number | null;
  provider_credential_id: string | null;
  /** Set for rows created before entitlements existed, which belong to one
   *  tenant. Platform-created configurations have null. */
  owning_tenant_id: string | null;
  archived_at: string | null;
  /** Tenants currently granted this configuration. */
  tenant_ids: string[];
  /** Current-month spend, per granted tenant. `tokens_used_this_month` is
   *  null when the counter could not be read — deliberately distinct from 0,
   *  which would claim nothing has been spent. */
  tenant_usage: { tenant_id: string; tokens_used_this_month: number | null }[];
  created_at: string;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  visibility: Visibility;
  owner_membership_id: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  // Both nullable: a widget/visitor conversation has no assistant bound and
  // is owned by a visitor session, not a membership.
  assistant_id: string | null;
  membership_id: string | null;
  title?: string | null;
  status: ConversationStatus;
  created_at: string;
  last_message_at: string | null;
}

/** Mirrors `MessageRole` in domain/ai_resources/entities.py. `user`/`assistant`
 *  are the visitor and the AI; `agent_message`/`internal_comment` are a human
 *  reply and a staff-only note; `system_event` is a transfer/handoff marker. */
export type MessageRole =
  | "user"
  | "assistant"
  | "agent_message"
  | "internal_comment"
  | "system_event";

export interface ConversationMessage {
  id: string;
  seq: number;
  role: MessageRole;
  content: string;
  /** Only what this answer cited -- label, document id and location. */
  citations: { label: string; document_id: string; source_location: string | null }[];
  /** The exchange's token cost, on the answer that incurred it. 0 on a
   *  question, and on any answer the provider reported no usage for. */
  token_count: number;
  created_at: string;
}

export interface ConversationThread {
  conversation: Conversation;
  /** False when reached through the oversight permission -- the UI shows a
   *  banner and hides the owner-only rename/delete controls. */
  is_owner: boolean;
  messages: ConversationMessage[];
  /** The visitor is composing something right now. Ephemeral state read from a
   *  short-lived cache key, deliberately not a `ConversationMessage`: it is not
   *  something anybody said and has no place in a transcript. */
  visitor_typing?: boolean;
  /** Turns in the whole thread, not in this page. */
  total_messages?: number;
  /** Older turns exist above this page. Derived server-side: a page that
   *  happens to be exactly `limit` long is otherwise indistinguishable from
   *  the last one. */
  has_more?: boolean;
}

export interface AnswerCitation {
  label: string;
  document_id: string;
  source_location: string | null;
  relevance: number;
}

export interface KnowledgeBaseQueryHit {
  document_id: string;
  filename: string;
  score: number;
}

// ---- Chatbot configuration, plan and handoff ----

export type Personality = "neutral" | "friendly" | "reassuring" | "professional";
export type ResponseLength = "concise" | "balanced" | "detailed";
export type ConversationState =
  | "ai_active"
  | "handoff_requested"
  | "unassigned"
  | "assigned"
  | "human_active"
  | "resolved";

/** Avatar *keys*, never URLs — the widget maps each to an inline SVG it already
 *  ships. A URL would let a tenant point every visitor's browser at an
 *  arbitrary third-party origin from their own customers' pages. */
export const AVATAR_KEYS = [
  "nursery-default",
  "nursery-bear",
  "nursery-star",
  "nursery-leaf",
] as const;
export type AvatarKey = (typeof AVATAR_KEYS)[number];

export interface ChatbotSettings {
  ai_chatbot_enabled: boolean;
  /** Resolved server-side: the tenant's own value, or the shipped default.
   *  Saving persists what the administrator was shown, so the prompt and the
   *  form never describe the assistant differently. */
  company_name: string | null;
  company_description: string;
  industry: string;
  /** The shipped role brief and restrictions, named for this company. Served
   *  rather than restated here, because they are the exact strings the prompt
   *  builder falls back to and a copy would drift invisibly. */
  default_role: string;
  default_avoid: string;
  allow_human_handoff: boolean;
  add_ai_summary_as_internal_comment: boolean;
  allow_ai_for_unassigned_conversations: boolean;
  /** What the tenant asked for. null = inherit the platform ceiling. */
  daily_message_limit: number | null;
  /** What is actually enforced after clamping. Shown beside the request so an
   *  admin sees their 5,000 applied as 1,000 rather than meeting it as a 429. */
  effective_daily_message_limit: number | null;
  share_visitor_location: boolean;
  /** The tenant's own brief, raw ("" when never written) so the form can show
   *  `default_role`/`default_avoid` as the starting point rather than claiming
   *  the tenant typed them. Saved through a separate behaviour endpoint. */
  role_instructions: string;
  avoid_instructions: string;
  personality: Personality;
  response_length: ResponseLength;
  updated_at: string;
}

/** The tenant's own plan. Usage fields are `number | null`: null means the
 *  counter could not be read — deliberately distinct from 0, which would claim
 *  nothing has been spent. Render `?`, never a reassuring zero. */
export interface TenantPlan {
  max_knowledge_bases: number | null;
  max_chat_widgets: number | null;
  max_messages_per_day: number | null;
  max_tokens_per_month: number | null;
  allow_invite_members: boolean;
  allow_create_roles: boolean;
  knowledge_bases_used: number;
  chat_widgets_used: number;
  messages_used_today: number | null;
  tokens_used_this_month: number | null;
  effective_daily_message_limit: number | null;
}

export interface TenantEntitlements {
  tenant_id: string;
  max_knowledge_bases: number | null;
  max_chat_widgets: number | null;
  max_messages_per_day: number | null;
  max_tokens_per_month: number | null;
  allow_invite_members: boolean;
  allow_create_roles: boolean;
  updated_at: string;
}

export interface ProviderCapability {
  provider: string;
  label: string;
  /** False = this platform has no adapter. Configuration is refused server-side;
   *  the UI disables it rather than hiding it, so an operator asking "why can't
   *  I pick Gemini?" sees the answer. */
  supported: boolean;
  supports_embeddings: boolean;
  supports_embedding_dimensions: boolean;
  supports_reasoning_effort: boolean;
  supports_request_timeout: boolean;
}

export interface Team {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  member_ids: string[];
}

export interface ChatbotBehaviour {
  role_instructions: string;
  avoid_instructions: string;
  personality: Personality;
  response_length: ResponseLength;
}

export interface WidgetPresentation {
  widget_id: string;
  chatbot_name: string;
  chatbot_title: string;
  avatar_key: AvatarKey;
  greeting: string | null;
  show_quick_reply_suggestions: boolean;
}

export interface UnassignedConversation {
  id: string;
  assigned_team_id: string | null;
  handoff_reason: string | null;
  handoff_at: string | null;
  handoff_initiated_by: "visitor" | "ai" | "agent" | null;
  title: string | null;
  last_message_at: string | null;
}

// ---- Platform overview dashboard ----
//
// Every usage field is `number | null`: null means the counter could not be
// read, deliberately distinct from 0, which would claim nothing has been
// spent. Render `?`, never a reassuring zero.
//
// `running_low` and the `remaining_*` values are computed server-side on
// purpose — the platform screen and the tenant's own screen must not disagree
// about what "running low" means, and a second copy of the threshold in
// TypeScript would drift the moment either side is edited.

export interface ProviderSpend {
  provider: string;
  model_count: number;
  /** Sum of per-tenant budgets. null when some model has no budget at all, in
   *  which case a total would silently exclude the biggest spender. */
  total_tokens: number | null;
  used_tokens: number | null;
  remaining_tokens: number | null;
  running_low: boolean;
  has_unbudgeted: boolean;
}

export interface TenantModelSpend {
  model_configuration_id: string;
  model_name: string;
  provider: string;
  token_budget_per_month: number | null;
  used_tokens: number | null;
}

export interface TenantSpend {
  tenant_id: string;
  slug: string;
  display_name: string;
  max_tokens_per_month: number | null;
  used_tokens: number | null;
  remaining_tokens: number | null;
  running_low: boolean;
  max_messages_per_day: number | null;
  used_messages_today: number | null;
  remaining_messages_today: number | null;
  models: TenantModelSpend[];
}

export interface PlatformOverview {
  providers: ProviderSpend[];
  /** Tenants running low come first — the server decides the ordering so every
   *  client puts what needs attention at the top. */
  tenants: TenantSpend[];
  tenants_running_low: number;
  /** Tokens spent on the platform default model, which belongs to no
   *  configuration and so appears in no provider row. Providers +
   *  unattributed = total tenant spend. null when a counter was unreadable. */
  unattributed_tokens: number | null;
  /** The threshold the flags were computed with, so the UI can explain why a
   *  row is highlighted instead of restating a number that could drift. */
  low_remaining_fraction: number;
}
