from __future__ import annotations


class TenantAuthzError(Exception):
    pass


class RoleNotFoundError(TenantAuthzError):
    pass


class MembershipNotFoundError(TenantAuthzError):
    pass


class PermissionNotFoundError(TenantAuthzError):
    pass


class SelfEscalationError(TenantAuthzError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class PermissionDeniedError(TenantAuthzError):
    def __init__(self, required_permission: str) -> None:
        super().__init__(f"missing required permission: {required_permission}")
        self.required_permission = required_permission


class DuplicateRoleCodeError(TenantAuthzError):
    pass


class PermissionNotTenantCustomizableError(TenantAuthzError):
    pass


class SystemRoleImmutableError(TenantAuthzError):
    """A system role's (`is_system=True`) permission set is fixed. Editing it
    would silently change the meaning of a role the rest of the system --
    the bootstrap seed, other tenants' assumptions about what "Tenant Owner"
    means -- was built expecting to be constant. Define a custom role instead."""
