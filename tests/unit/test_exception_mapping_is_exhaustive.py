"""Every application exception must have an explicit HTTP status.

This exists because the same defect has now been introduced twice: an
exception declared next to its use case instead of in its module's
`exceptions.py`, so `register_exception_handlers` never matched it and a
permission denial reached the client as a 500. The first instance
(`TenantCreationDeniedError`, `DuplicateSlugError`) shipped and was found by
clicking "Suspend" in the admin console; the second
(`UserManagementDeniedError`) was written during the fix for the first.

The handlers *do* fall back to 400 for an unmapped member of a known
hierarchy, so the failure is quiet rather than loud -- a denial reported as
"Bad Request" is wrong in a way nobody notices until a UI shows the wrong
message. Hence a test rather than a convention.
"""

from __future__ import annotations

import pytest

from iam_platform.api import exception_handlers as handlers
from iam_platform.application.ai_resources import exceptions as ai_resource_errors
from iam_platform.application.identity import exceptions as identity_errors
from iam_platform.application.impersonation import exceptions as impersonation_errors
from iam_platform.application.platform_authz import exceptions as platform_authz_errors
from iam_platform.application.tenancy import exceptions as tenancy_errors
from iam_platform.application.tenant_authz import exceptions as tenant_authz_errors

pytestmark = pytest.mark.unit


def _concrete_subclasses(base: type[Exception]) -> set[type[Exception]]:
    found: set[type[Exception]] = set()
    for subclass in base.__subclasses__():
        found.add(subclass)
        found |= _concrete_subclasses(subclass)
    return found


HIERARCHIES = [
    pytest.param(
        identity_errors.ApplicationError,
        handlers._IDENTITY_STATUS_MAP,
        id="identity",
    ),
    pytest.param(
        platform_authz_errors.PlatformAuthzError,
        handlers._PLATFORM_AUTHZ_STATUS_MAP,
        id="platform_authz",
    ),
    pytest.param(tenancy_errors.TenancyError, handlers._TENANCY_STATUS_MAP, id="tenancy"),
    pytest.param(
        tenant_authz_errors.TenantAuthzError,
        handlers._TENANT_AUTHZ_STATUS_MAP,
        id="tenant_authz",
    ),
    pytest.param(
        ai_resource_errors.AiResourceError,
        handlers._AI_RESOURCE_STATUS_MAP,
        id="ai_resources",
    ),
    pytest.param(
        impersonation_errors.ImpersonationError,
        handlers._IMPERSONATION_STATUS_MAP,
        id="impersonation",
    ),
]


@pytest.mark.parametrize(("base", "status_map"), HIERARCHIES)
def test_every_application_exception_has_an_explicit_status(
    base: type[Exception], status_map: dict[type[Exception], int]
) -> None:
    # Importing the use-case modules is what makes their exception subclasses
    # exist; the API package pulls all of them in transitively via the routers.
    import iam_platform.api.main  # noqa: F401

    unmapped = sorted(
        exc.__name__ for exc in _concrete_subclasses(base) if exc not in status_map
    )
    assert not unmapped, (
        f"{base.__name__} subclasses with no entry in the status map: {unmapped}. "
        "They will fall back to 400 instead of their real status. Add them to the "
        "map in api/exception_handlers.py."
    )


def test_no_application_exception_is_declared_outside_its_exceptions_module() -> None:
    """The root cause, caught directly.

    An exception defined in a use-case module is easy to raise and easy to
    forget to map. Keeping them all in one `exceptions.py` per bounded context
    is what makes the exhaustiveness check above reviewable.
    """
    import iam_platform.api.main  # noqa: F401

    misplaced: list[str] = []
    for base in (
        identity_errors.ApplicationError,
        platform_authz_errors.PlatformAuthzError,
        tenancy_errors.TenancyError,
        tenant_authz_errors.TenantAuthzError,
        ai_resource_errors.AiResourceError,
        impersonation_errors.ImpersonationError,
    ):
        for exc in _concrete_subclasses(base):
            if not exc.__module__.endswith(".exceptions"):
                misplaced.append(f"{exc.__name__} in {exc.__module__}")

    assert not misplaced, (
        "Application exceptions declared outside an `exceptions.py`: "
        f"{sorted(misplaced)}. Move them so the mapping stays reviewable in one place."
    )
