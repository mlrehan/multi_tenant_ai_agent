from __future__ import annotations


class PlatformAuthzError(Exception):
    pass


class RoleNotFoundError(PlatformAuthzError):
    pass


class PlatformPermissionNotFoundError(PlatformAuthzError):
    pass


class SelfEscalationError(PlatformAuthzError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class UserNotFoundError(PlatformAuthzError):
    pass


class TenantCreationDeniedError(PlatformAuthzError):
    """Actor lacks the platform permission gating a tenant lifecycle action.

    Lives here, in the PlatformAuthzError hierarchy, rather than beside the
    use case in manage_tenants.py where it started life -- as a bare
    `Exception` subclass it matched no handler in
    api/exception_handlers.py, so every permission-denied tenant
    suspend/create surfaced to the client as a 500 instead of a 403.
    """


class DuplicateSlugError(PlatformAuthzError):
    pass


class TenantNotFoundError(PlatformAuthzError):
    pass


class TenantListDeniedError(PlatformAuthzError):
    """Actor may not enumerate tenants.

    Third instance of the misplaced-exception bug, and the one that proved the
    guard test earns its keep: it was declared beside its use case, matched no
    entry in the status map, and so answered 400 "Bad Request" to what is
    plainly a 403.
    """


class DuplicateEmailError(PlatformAuthzError):
    """An administrator-created or renamed account collides with an existing one.

    Reported plainly, unlike self-service registration which deliberately hides
    it -- see `CreateUser`'s docstring for why the enumeration argument doesn't
    apply to a caller who already holds `platform.users.read`.
    """


class WeakPasswordError(PlatformAuthzError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class UserManagementDeniedError(PlatformAuthzError):
    """Actor lacks the permission gating a platform user-directory action, or
    attempted something refused outright (suspending their own account).

    Declared here rather than beside its use case for the same reason as
    TenantCreationDeniedError above: an exception that isn't in this hierarchy
    is an exception api/exception_handlers.py cannot map, and it reaches the
    client as a 500.
    """


class DuplicatePlatformRoleCodeError(PlatformAuthzError):
    pass


class SystemPlatformRoleImmutableError(PlatformAuthzError):
    """A system platform role's (`is_system=True`) permission set is fixed --
    same reasoning as `tenant_authz.SystemRoleImmutableError`: editing it would
    silently change what a role every deployment assumes is constant (the
    bootstrap-created `platform_super_admin`, in particular) actually grants."""


class TenantOwnerRoleNotSeededError(PlatformAuthzError):
    """The `tenant_owner` catalog role (`tenant_roles` row with
    `tenant_id IS NULL`) doesn't exist, so `CreateTenant` cannot grant it to
    the new owner.

    This is a deployment-configuration problem, not a client error:
    `scripts/bootstrap_tenant_catalog.py` was never run. Before this
    exception existed, `CreateTenant` looked the role up, found nothing, and
    silently created the tenant anyway -- the owner ended up with an active
    membership and zero permissions, invisible until someone noticed the
    console had nothing to show them. Refusing outright surfaces the
    misconfiguration at the moment it matters instead of at some later
    support ticket.
    """
