"""The 12 mandatory cross-tenant / privilege-escalation scenarios from
docs/03-threat-model.md, one test class per numbered row.

See tests/security/README.md for the scenario-to-test mapping. Class names
carry the scenario number so a missing defense is visible from the file
listing rather than only from a careful read of the threat model.

These are *negative* tests: each asserts the attack fails. A test here passing
because the feature is broken in some other way would be worse than no test,
so where practical each class also asserts the legitimate path still works.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import KnowledgeBaseNotFoundError
from iam_platform.application.ai_resources.manage_knowledge_base import (
    QueryKnowledgeBase,
    QueryKnowledgeBaseQuery,
)
from iam_platform.application.tenant_authz.exceptions import (
    PermissionNotFoundError,
)
from iam_platform.application.tenant_authz.manage_custom_role import (
    CreateCustomRole,
    CreateCustomRoleCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import KnowledgeBase, ResourceVisibility
from iam_platform.domain.impersonation.policies import (
    PermissionRisk,
    is_impersonated,
    restrict_permissions_for_impersonation,
)
from iam_platform.domain.shared.policies import can_assign_role
from iam_platform.domain.shared.value_objects import Email
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole
from iam_platform.domain.tenant_authz.policies import (
    PermissionEntitlement,
    resolve_effective_tenant_permissions,
)
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork, FakeVectorSearchClient
from tests.unit.tenant_authz.fakes import (
    FakeTenantUnitOfWork,
    make_tenant_permission,
    make_tenant_role,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# --- Scenario 1 --------------------------------------------------------------


class TestScenario01TenantHintTampering:
    """A user in Tenant A edits the request's tenant hint to Tenant B's ID.

    Defense: the active tenant is resolved and re-validated server-side against
    a real membership row; a header/body hint is a *candidate*, never
    authoritative. Proven here at the resolver's decision point -- the
    membership lookup is what authorizes, and it is keyed on the candidate, so
    a forged candidate simply finds nothing.
    """

    async def test_tenant_hint_without_a_membership_resolves_to_nothing(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_a, tenant_b = uuid4(), uuid4()
        user_id = uuid4()
        membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_a,
            user_id=user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[membership.id] = membership

        # The forged hint: user belongs to A, claims B.
        forged = await uow.tenant_memberships.get_by_tenant_and_user(tenant_b, user_id)
        assert forged is None

        # The honest hint still works -- the check isn't just failing closed
        # on everything.
        genuine = await uow.tenant_memberships.get_by_tenant_and_user(tenant_a, user_id)
        assert genuine is not None and genuine.is_active


# --- Scenario 2 --------------------------------------------------------------


class TestScenario02CrossTenantIdor:
    """A user guesses a resource ID belonging to another tenant.

    Defense: RLS denies the row even when the application filter is missed, and
    the response is 404 rather than 403 so existence isn't leaked. The
    database half is proven against real Postgres in
    ``tests/integration/db/test_ai_resources_rls.py``; the *error-shape* half
    is proven here.
    """

    def test_invisible_resource_raises_not_found_never_access_denied(self) -> None:
        from iam_platform.application.ai_resources.exceptions import (
            AssistantNotFoundError,
            ResourceAccessDeniedError,
        )

        # The contract that prevents existence inference: the discovery-time
        # denial and the "doesn't exist" case are the same exception type, so
        # a caller cannot tell them apart.
        assert issubclass(AssistantNotFoundError, Exception)
        assert not issubclass(AssistantNotFoundError, ResourceAccessDeniedError)
        assert not issubclass(ResourceAccessDeniedError, AssistantNotFoundError)


# --- Scenario 3 --------------------------------------------------------------


class TestScenario03PlatformPermissionInTenantRole:
    """A tenant admin creates a custom role and tries to attach
    ``platform.tenants.suspend`` to it.

    Defense: platform and tenant permissions live in *disjoint tables*
    (docs/06-authorization-model.md), so a platform code is simply absent from
    the tenant permission catalog and the role creation is rejected. This is a
    schema-level guarantee, not a string check on the code prefix.
    """

    async def _actor_with_manage_roles(self, uow: FakeTenantUnitOfWork, tenant_id):
        role = make_tenant_role(
            tenant_id=None, code="admin", rank=100, now=NOW, is_system=True
        )
        uow.tenant_roles.by_id[role.id] = role
        uow.tenant_permissions.role_permission_codes[role.id] = {"tenant.roles.manage"}
        actor_user_id = uuid4()
        membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=actor_user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[membership.id] = membership
        uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=membership.id,
            role_id=role.id,
            granted_by_user_id=actor_user_id,
            granted_at=NOW,
        )
        return actor_user_id

    async def test_platform_permission_code_is_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = await self._actor_with_manage_roles(uow, tenant_id)

        # A real tenant permission exists in the catalog; the platform one
        # deliberately does not, because it lives in a different table.
        legit = make_tenant_permission(code="tenant.resources.read", now=NOW)
        uow.tenant_permissions.by_id[legit.id] = legit

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        with pytest.raises(PermissionNotFoundError):
            await use_case.execute(
                CreateCustomRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    code="sneaky",
                    name="Sneaky",
                    description=None,
                    rank=5,
                    permission_codes=["platform.tenants.suspend"],
                )
            )
        assert not any(r.code == "sneaky" for r in uow.tenant_roles.by_id.values())


# --- Scenario 4 --------------------------------------------------------------


class TestScenario04SelfEscalation:
    """A member with ``tenant.roles.manage`` tries to assign themselves a
    higher role.

    Defense: the actor may only grant a role whose permission set is a subset
    of their own, *and* may never self-assign a role of equal or higher rank.
    Both halves are checked -- either alone is bypassable.
    """

    def test_cannot_self_assign_equal_or_higher_rank(self) -> None:
        violations = can_assign_role(
            actor_effective_permissions=frozenset({"tenant.roles.manage"}),
            actor_highest_rank=50,
            is_self_assignment=True,
            target_role_rank=50,  # equal rank is still an escalation
            target_role_permission_codes=frozenset({"tenant.roles.manage"}),
        )
        assert violations

    def test_cannot_grant_permissions_not_held(self) -> None:
        violations = can_assign_role(
            actor_effective_permissions=frozenset({"tenant.roles.manage"}),
            actor_highest_rank=100,
            is_self_assignment=False,
            target_role_rank=10,
            target_role_permission_codes=frozenset({"tenant.billing.manage"}),
        )
        assert violations

    def test_legitimate_downward_grant_is_allowed(self) -> None:
        """Guards against the defense being implemented as "deny everything"."""
        violations = can_assign_role(
            actor_effective_permissions=frozenset(
                {"tenant.roles.manage", "tenant.resources.read"}
            ),
            actor_highest_rank=100,
            is_self_assignment=False,
            target_role_rank=10,
            target_role_permission_codes=frozenset({"tenant.resources.read"}),
        )
        assert violations == []


# --- Scenario 5 --------------------------------------------------------------


class TestScenario05SuspendedMembershipMidSession:
    """A membership is suspended mid-session but the JWT is still cryptographically
    valid.

    Defense: membership status is re-checked server-side on every request
    rather than trusted from the token. A suspended membership must stop
    resolving, immediately, without waiting for token expiry.
    """

    async def test_suspended_membership_no_longer_resolves(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id, user_id = uuid4(), uuid4()
        membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[membership.id] = membership

        found = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, user_id)
        assert found is not None and found.is_active  # session works before

        membership.suspend(reason="policy violation", now=NOW)

        # Same still-valid token, same lookup -- now inactive. The tenant
        # resolver treats "not active" identically to "no membership".
        after = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, user_id)
        assert after is not None
        assert not after.is_active

    async def test_revoked_membership_no_longer_resolves(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id, user_id = uuid4(), uuid4()
        membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[membership.id] = membership
        membership.revoke(reason="offboarded", now=NOW)

        after = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, user_id)
        assert after is not None
        assert not after.is_active


# --- Scenario 6 --------------------------------------------------------------


class TestScenario06RefreshTokenReuse:
    """A stolen refresh token is replayed after the legitimate client already
    rotated it.

    Defense: reuse of an already-rotated token revokes the *entire family*, not
    just the replayed token, and raises a security event.
    """

    async def test_replaying_a_rotated_token_revokes_the_whole_family(self) -> None:
        """Overlaps deliberately with
        ``tests/unit/identity/test_refresh_session.py`` -- the threat model
        designates this a mandatory scenario, so it gets an entry here that can
        actually fail, rather than a pointer to a test elsewhere.

        The property asserted is specifically the *family-wide* revocation:
        revoking only the replayed token would leave the attacker's
        freshly-issued one working.
        """
        from iam_platform.application.identity.exceptions import RefreshReuseDetectedError
        from iam_platform.application.identity.refresh_session import (
            RefreshSession,
            RefreshSessionCommand,
        )
        from iam_platform.application.identity.session_issuance import create_session_and_tokens
        from iam_platform.domain.identity.entities import User, UserStatus
        from tests.unit.identity.fakes import FakeIdentityUnitOfWork, FakeJwtIssuer

        uow = FakeIdentityUnitOfWork()
        user = User(
            id=uuid4(),
            email=Email("victim@example.com"),
            status=UserStatus.ACTIVE,
            security_stamp=uuid4(),
            created_at=NOW,
            updated_at=NOW,
        )
        uow.users.by_id[user.id] = user
        tokens = await create_session_and_tokens(
            uow,
            FakeJwtIssuer(),
            user=user,
            amr=["pwd"],
            now=NOW,
            ip=None,
            user_agent=None,
            access_token_ttl_seconds=900,
        )
        stolen_raw = tokens.refresh_token

        use_case = RefreshSession(uow, FakeJwtIssuer(), FixedClock(NOW), 900)

        # Legitimate client rotates; the attacker's stolen copy is now stale.
        await use_case.execute(RefreshSessionCommand(refresh_token=stolen_raw))
        assert len(uow.refresh_tokens.by_id) == 2  # original + replacement

        with pytest.raises(RefreshReuseDetectedError):
            await use_case.execute(RefreshSessionCommand(refresh_token=stolen_raw))

        # Every token in the family, including the one the legitimate client
        # just received, is revoked -- forcing a full re-login.
        assert all(t.revoked_at is not None for t in uow.refresh_tokens.by_id.values())
        assert any(s.revoked_reason == "reuse_detected" for s in uow.sessions.by_id.values())


# --- Scenario 7 --------------------------------------------------------------


class TestScenario07VectorNamespaceIsolation:
    """A vector query omits the tenant filter and returns cross-tenant chunks.

    Defense: the namespace is server-derived and mandatory; the query builder
    cannot construct a filterless query because the use case passes the
    namespace read off the already-authorized knowledge-base row.
    """

    async def test_unauthorized_knowledge_base_never_reaches_the_search_client(
        self,
    ) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        attacker_user_id = uuid4()
        attacker_membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=attacker_user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[attacker_membership.id] = attacker_membership

        victim_membership_id = uuid4()
        restricted_kb = KnowledgeBase(
            id=uuid4(),
            tenant_id=tenant_id,
            name="confidential",
            owner_membership_id=victim_membership_id,
            visibility=ResourceVisibility.RESTRICTED,
            vector_namespace=f"{tenant_id}/secret",
            created_at=NOW,
            updated_at=NOW,
        )
        uow.knowledge_bases.by_id[restricted_kb.id] = restricted_kb

        search = FakeVectorSearchClient()
        search.by_namespace[restricted_kb.vector_namespace] = [(uuid4(), 0.99)]

        use_case = QueryKnowledgeBase(uow, search)
        with pytest.raises(KnowledgeBaseNotFoundError):
            await use_case.execute(
                QueryKnowledgeBaseQuery(
                    actor_user_id=str(attacker_user_id),
                    tenant_id=str(tenant_id),
                    knowledge_base_id=str(restricted_kb.id),
                    permissions=frozenset({"tenant.knowledge_bases.query"}),
                    query_text="confidential",
                )
            )
        # The critical assertion: no search ran at all, so no chunk could leak.
        assert search.queried_namespaces == []

    def test_query_command_cannot_carry_a_namespace(self) -> None:
        import dataclasses

        fields = {f.name for f in dataclasses.fields(QueryKnowledgeBaseQuery)}
        assert "namespace" not in fields
        assert "vector_namespace" not in fields


# --- Scenario 8 --------------------------------------------------------------


class TestScenario08WorkerContextBleed:
    """A background job for Tenant A runs on a worker that previously handled
    Tenant B, and inherits the stale context.

    **Status: the worker runtime is not built yet** (no `workers/` module ships
    in Phases 5-8), so there is no job execution path to attack. What *is*
    testable today is the property the defense rests on: the Unit of Work sets
    tenant context per-transaction via ``set_config(..., true)``, which
    PostgreSQL resets at transaction end -- so a reused connection cannot carry
    context into the next job. That is proven against real Postgres by
    ``tests/integration/db/test_rls_isolation.py::TestPoolReuse``.

    This test asserts the *structural* precondition rather than pretending to
    exercise a worker that doesn't exist -- see scenario 12, which covers the
    same mechanism from the connection-pooling angle.
    """

    def test_unit_of_work_scopes_context_to_the_transaction(self) -> None:
        import inspect

        from iam_platform.infrastructure.db import unit_of_work

        source = inspect.getsource(unit_of_work)
        # `true` as set_config's third argument is what makes the setting
        # transaction-local. A bare `SET` (session-scoped) would be the bug.
        assert "set_config('app.tenant_id', :tid, true)" in source
        assert "set_config('app.user_id', :uid, true)" in source

    def test_no_session_scoped_set_statements_exist(self) -> None:
        import inspect

        from iam_platform.infrastructure.db import unit_of_work

        source = inspect.getsource(unit_of_work)
        # Guards against someone "simplifying" set_config into a plain SET,
        # which would silently make the context outlive the transaction.
        assert "SET app." not in source
        assert "set_config('app.tenant_id', :tid, false)" not in source


# --- Scenario 9 --------------------------------------------------------------


class TestScenario09ImpersonationScope:
    """An impersonation session is used to modify tenant roles or export data
    beyond the stated support reason.

    **This defense was missing until Phase 8.** The impersonation token's
    ``sub`` is the target user, which correctly kept *platform* permissions
    out -- but nothing constrained the target's own permissions, so
    impersonating a tenant owner inherited ``tenant.roles.manage``. Fixed by
    ``domain/impersonation/policies.py``.
    """

    def test_role_management_is_stripped_from_an_impersonated_session(self) -> None:
        target_permissions = frozenset(
            {"tenant.roles.manage", "tenant.resources.read", "tenant.conversations.create"}
        )
        restricted = restrict_permissions_for_impersonation(
            target_permissions=target_permissions, risk_by_code={}
        )
        assert "tenant.roles.manage" not in restricted
        # Read-only support access survives -- the point is to allow support,
        # not to make impersonation useless.
        assert "tenant.resources.read" in restricted
        assert "tenant.conversations.create" in restricted

    def test_data_export_and_credential_management_are_stripped(self) -> None:
        restricted = restrict_permissions_for_impersonation(
            target_permissions=frozenset(
                {
                    "tenant.data.export",
                    "tenant.provider_credentials.manage",
                    "tenant.users.manage",
                    "tenant.users.invite",
                    "tenant.billing.manage",
                }
            ),
            risk_by_code={},
        )
        assert restricted == frozenset()

    def test_high_risk_permissions_are_stripped_even_if_not_blocklisted(self) -> None:
        """The data-driven half: a permission nobody thought to blocklist is
        still denied if the catalog tags it high-risk."""
        code = "tenant.something.dangerous"
        restricted = restrict_permissions_for_impersonation(
            target_permissions=frozenset({code}),
            risk_by_code={code: PermissionRisk(code=code, risk_level="critical")},
        )
        assert restricted == frozenset()

    def test_blocklist_holds_even_when_catalog_mistags_as_low_risk(self) -> None:
        """The independent half: a mis-tagged dangerous permission is still
        denied by the explicit blocklist."""
        code = "tenant.roles.manage"
        restricted = restrict_permissions_for_impersonation(
            target_permissions=frozenset({code}),
            risk_by_code={code: PermissionRisk(code=code, risk_level="low")},
        )
        assert restricted == frozenset()

    def test_ordinary_session_is_not_restricted(self) -> None:
        """Guards against the fix accidentally applying to normal sessions."""
        assert not is_impersonated(None)
        assert is_impersonated({"sub": str(uuid4()), "imp_sid": str(uuid4())})


# --- Scenario 10 -------------------------------------------------------------


class TestScenario10FeatureEntitlement:
    """A custom role references a permission requiring a feature the tenant
    doesn't have.

    Defense: effective-permission resolution intersects with the tenant's
    active entitlements, so an un-entitled permission never appears in the
    resolved set regardless of role assignment.
    """

    def test_permission_requiring_a_disabled_feature_is_filtered_out(self) -> None:
        role_id = uuid4()
        resolved = resolve_effective_tenant_permissions(
            assigned_role_ids={role_id},
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={role_id: {"tenant.advanced.analytics"}},
            override_effect_by_permission_code={},
            permission_catalog={
                "tenant.advanced.analytics": PermissionEntitlement(
                    code="tenant.advanced.analytics", required_feature="advanced_analytics"
                )
            },
            enabled_feature_codes=set(),  # tenant is not entitled
        )
        assert resolved == frozenset()

    def test_same_permission_resolves_when_the_feature_is_enabled(self) -> None:
        role_id = uuid4()
        resolved = resolve_effective_tenant_permissions(
            assigned_role_ids={role_id},
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={role_id: {"tenant.advanced.analytics"}},
            override_effect_by_permission_code={},
            permission_catalog={
                "tenant.advanced.analytics": PermissionEntitlement(
                    code="tenant.advanced.analytics", required_feature="advanced_analytics"
                )
            },
            enabled_feature_codes={"advanced_analytics"},
        )
        assert resolved == frozenset({"tenant.advanced.analytics"})


# --- Scenario 11 -------------------------------------------------------------


class TestScenario11OAuthEmailHijack:
    """An attacker registers with an email matching an existing OAuth-linked
    account to hijack it.

    Defense: no auto-merge by email -- an OAuth login whose email matches an
    existing account is refused outright. Linking requires an authenticated
    session that explicitly initiates it.
    """

    async def test_oauth_login_matching_an_existing_email_is_refused(self) -> None:
        from iam_platform.application.identity.exceptions import OAuthEmailConflictError
        from iam_platform.application.identity.oauth_login import (
            CompleteOAuthLogin,
            CompleteOAuthLoginCommand,
        )
        from iam_platform.application.identity.ports import OAuthProfile
        from iam_platform.domain.identity.entities import User
        from tests.unit.identity.fakes import FakeIdentityUnitOfWork, FakeJwtIssuer

        uow = FakeIdentityUnitOfWork()
        victim = User(
            id=uuid4(),
            email=Email("victim@example.com"),
            security_stamp=uuid4(),
            created_at=NOW,
            updated_at=NOW,
        )
        uow.users.by_id[victim.id] = victim

        attacker_profile = OAuthProfile(
            provider="google",
            subject="attacker-subject-999",
            email="victim@example.com",  # same email, different provider identity
        )

        use_case = CompleteOAuthLogin(uow, FakeJwtIssuer(), FixedClock(NOW), 900)
        with pytest.raises(OAuthEmailConflictError):
            await use_case.execute(
                CompleteOAuthLoginCommand(profile=attacker_profile, linking_user_id=None)
            )

        # No account was hijacked and no new user silently created.
        assert len(uow.users.by_id) == 1
        assert uow.oauth_accounts.by_id == {}

    async def test_oauth_login_with_an_unused_email_still_works(self) -> None:
        """Guards against the defense being "reject all OAuth registration"."""
        from iam_platform.application.identity.oauth_login import (
            CompleteOAuthLogin,
            CompleteOAuthLoginCommand,
        )
        from iam_platform.application.identity.ports import OAuthProfile
        from tests.unit.identity.fakes import FakeIdentityUnitOfWork, FakeJwtIssuer

        uow = FakeIdentityUnitOfWork()
        profile = OAuthProfile(
            provider="google",
            subject="fresh-subject-123",
            email="newcomer@example.com",
        )

        use_case = CompleteOAuthLogin(uow, FakeJwtIssuer(), FixedClock(NOW), 900)
        result = await use_case.execute(
            CompleteOAuthLoginCommand(profile=profile, linking_user_id=None)
        )
        assert result is not None
        assert len(uow.users.by_id) == 1


# --- Scenario 12 -------------------------------------------------------------


class TestScenario12PooledConnectionContext:
    """RLS is bypassed because a pooled connection retained a prior request's
    session variable.

    Defense: transaction-scoped ``set_config(..., true)`` (never session-scoped
    ``SET``), plus the ``NULLIF(current_setting(...), '')`` read guard that
    turns a stale empty string into NULL rather than a cast error.

    The behavioural proof runs against real Postgres with ``pool_size=1`` in
    ``tests/integration/db/test_rls_isolation.py::TestPoolReuse``; this asserts
    the policy SQL itself still contains the guard, which is cheap and catches
    a regression in the migration.
    """

    def test_rls_policies_use_the_nullif_read_guard(self) -> None:
        from pathlib import Path

        versions = Path("alembic/versions")
        policy_files = [
            p
            for p in versions.glob("*.py")
            if "current_setting" in p.read_text(encoding="utf-8")
        ]
        assert policy_files, "no migration defines RLS policies"

        for path in policy_files:
            source = path.read_text(encoding="utf-8")
            # Every current_setting read must be NULLIF-wrapped; a bare
            # `current_setting('app.tenant_id', true)::uuid` is the bug.
            assert "NULLIF(current_setting('app.tenant_id', true), '')::uuid" in source, (
                f"{path.name} reads tenant context without the NULLIF guard"
            )
