# Requirements

## Functional Requirements

- **FR-AUTH**: registration, email verification, password login, password reset, MFA/WebAuthn-ready, social OIDC login (Google, Facebook) with safe linking/unlinking, session + device management, logout / logout-everywhere.
- **FR-TOKEN**: JWT access + rotating refresh tokens, reuse detection, revocation, API keys (hashed at rest) for service-to-service/integration use.
- **FR-TENANT**: create/join/leave tenant, multi-tenant membership, invitations, per-membership roles, department/team assignment, suspension/revocation independent of the global user account.
- **FR-RBAC**: platform RBAC, tenant RBAC, hierarchical roles, system + custom roles, effective-permission computation, explicit allow/deny, ownership/team/department-scoped rules.
- **FR-SUPPORT**: platform-initiated impersonation with reason, duration, optional approval, full audit trail, visible indicator.
- **FR-AI**: assistant/knowledge-base/conversation authorization, tenant-scoped vector namespaces, provider-credential access control, usage/budget enforcement.
- **FR-AUDIT**: immutable-append audit log for all security-sensitive actions (see [03-threat-model.md](03-threat-model.md) and audit list below).
- **FR-API**: versioned REST endpoints (auth/sessions, tenant management, memberships/invitations, platform admin, roles/permissions, effective-permission inspection, assistants/knowledge bases, audit/security events, impersonation, health/readiness/liveness/metrics) with OpenAPI schema.

## Non-Functional Requirements

| Category | Requirement |
|---|---|
| Scale | Thousands of tenants, millions of users, horizontal API/worker scaling |
| Availability | API stateless and horizontally scalable; no in-memory session affinity |
| Latency | Authorization decision (cached path) < 10ms p95; uncached DB path < 50ms p95 |
| Consistency | Permission/role changes visible within one permission-version cache TTL (target ≤30s) or immediately for high-risk changes (suspension, revocation) via active invalidation |
| Security | OWASP ASVS L2+ baseline; Argon2id; TLS everywhere; secrets never in code or logs |
| Auditability | Every privilege change and cross-tenant action is immutably logged with actor/effective-user distinction |
| Portability | IdP-agnostic auth core; policy-engine-agnostic authorization core (interfaces ready for OPA/Cedar) |
| Observability | Structured logs, metrics, tracing, correlation IDs propagated through API → worker → DB |
| Maintainability | Clear `api/application/domain/infrastructure/core/workers` boundaries; no framework leakage into domain layer |

## Audit requirement list (FR-AUDIT detail)

Security-sensitive actions that must be audited: login success/failure, password/MFA changes, tenant creation/suspension, invitations and membership changes, role/permission changes, API-key operations, provider credential changes, assistant access changes, impersonation, data exports, cross-tenant platform actions.

Each audit record must include: actor, effective user, tenant, action, resource type/ID, before/after state where appropriate, result and failure reason, timestamp, IP and user agent, request/correlation IDs, impersonation session (if applicable), metadata. Audit logs must be protected from unauthorized modification (append-only at the DB grant level).
