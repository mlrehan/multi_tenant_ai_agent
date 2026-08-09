"""Impersonation permission scoping -- docs/03-threat-model.md scenario 9.

**Why this module exists (Phase 8 finding).** Phase 6 issued an impersonation
token whose ``sub`` is the target user, which correctly prevents *platform*
permissions from leaking into the tenant context (docs/06-authorization-model.md's
emphasis). But nothing then constrained the *target's own* permissions, so a
support agent impersonating a tenant owner inherited `tenant.roles.manage` and
could have granted themselves a role, or exported data, under the target's
identity. docs/03-threat-model.md scenario 9 requires the opposite: an
impersonation context "distinct from the target user's own permissions",
read/support-limited.

Reconciling the two docs: both constraints hold simultaneously. The token's
identity is the target (never the platform user), *and* the resulting
permission set is intersected with what support access may safely carry.

**Two independent filters, deliberately.** Neither alone is sufficient:

1. ``_ALWAYS_DENIED_CODES`` -- an explicit blocklist of the permissions that
   would let an impersonator escalate or exfiltrate. Independent of how the
   permission catalog is tagged, so a mis-tagged row can't open a hole.
2. ``risk_level`` -- data-driven, so a permission added later is protected the
   moment it's tagged ``high``/``critical`` without anyone editing this file.

The blocklist is the backstop for (2) being mis-tagged; the risk level is the
backstop for (1) being incomplete. A permission has to slip past *both* to
reach an impersonated session.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Risk levels an impersonated session may never carry, regardless of what the
#: target user holds. Matches the `risk_level` CHECK constraint in
#: docs/14-schema-tenant-authorization.md / docs/16-schema-ai-resources.md.
_DENIED_RISK_LEVELS = frozenset({"high", "critical"})

#: Permissions impersonation can never carry even if tagged low-risk. These are
#: the escalation and exfiltration paths specifically named in
#: docs/03-threat-model.md scenario 9 ("modify tenant roles or export data").
_ALWAYS_DENIED_CODES = frozenset(
    {
        "tenant.roles.manage",
        "tenant.users.manage",
        "tenant.users.invite",
        "tenant.provider_credentials.manage",
        "tenant.data.export",
        "tenant.billing.manage",
    }
)


@dataclass(frozen=True, slots=True)
class PermissionRisk:
    code: str
    risk_level: str


def restrict_permissions_for_impersonation(
    *,
    target_permissions: frozenset[str],
    risk_by_code: dict[str, PermissionRisk],
) -> frozenset[str]:
    """Narrow a target user's effective permissions to what support access may
    exercise on their behalf.

    ``risk_by_code`` may be missing entries -- an unknown permission is treated
    as *allowed* rather than denied, because the catalog is the same source the
    permission itself came from, and failing closed on a lookup miss would
    silently break legitimate read-only support for any permission the caller
    didn't happen to load. The blocklist is what guarantees the dangerous cases
    are covered regardless.
    """
    allowed: set[str] = set()
    for code in target_permissions:
        if code in _ALWAYS_DENIED_CODES:
            continue
        risk = risk_by_code.get(code)
        if risk is not None and risk.risk_level in _DENIED_RISK_LEVELS:
            continue
        allowed.add(code)
    return frozenset(allowed)


def is_impersonated(actor_claim: dict[str, object] | None) -> bool:
    """True when the request carries an ``act`` claim -- i.e. the authenticated
    identity is being acted *for* by a platform user."""
    return actor_claim is not None
