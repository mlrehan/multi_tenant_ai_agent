from __future__ import annotations


class TenancyError(Exception):
    pass


class TenantNotFoundError(TenancyError):
    pass


class MembershipNotFoundError(TenancyError):
    pass


class InvalidOrExpiredInvitationError(TenancyError):
    pass


class InvitationEmailMismatchError(TenancyError):
    """The authenticated user accepting the invitation doesn't match the
    invited email -- prevents redeeming someone else's invite link."""


class PermissionDeniedError(TenancyError):
    def __init__(self, required_permission: str) -> None:
        super().__init__(f"missing required permission: {required_permission}")
        self.required_permission = required_permission


class MembershipAlreadyExistsError(TenancyError):
    """The target user already has a membership row (in some status) for this
    tenant. `tenant_memberships` carries a unique `(tenant_id, user_id)`
    constraint, so a second row can never be inserted -- the caller needs to
    manage the existing one instead (reactivate/restore it) rather than add a
    new one."""
