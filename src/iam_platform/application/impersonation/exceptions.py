from __future__ import annotations


class ImpersonationError(Exception):
    pass


class ImpersonationDeniedError(ImpersonationError):
    def __init__(self, required_permission: str) -> None:
        super().__init__(f"missing required permission: {required_permission}")
        self.required_permission = required_permission


class ImpersonationTargetNotFoundError(ImpersonationError):
    pass


class ImpersonationSessionNotFoundError(ImpersonationError):
    pass
