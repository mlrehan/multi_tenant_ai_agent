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
  created_at: string;
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

export interface ModelConfiguration {
  id: string;
  tenant_id: string | null;
  model_name: string;
  is_platform_default: boolean;
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
  assistant_id: string;
  membership_id: string;
  title?: string | null;
  status: ConversationStatus;
  created_at: string;
  last_message_at: string | null;
}

export interface ProviderCredential {
  id: string;
  provider: string;
  key_hint: string;
  created_at: string;
  rotated_at: string | null;
  revoked_at: string | null;
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
