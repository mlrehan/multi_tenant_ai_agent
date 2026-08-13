"""Attaching a tenant's own provider key to a model they are entitled to.

The write half of BYOK. `test_byok_credentials.py` proves the *answer* path
honours an attachment; this proves only the right people can create one, and
only pointing at their own credential.

The composite FK `fk_tenant_model_configurations_provider_credential` enforces
the tenant-confinement independently, in Postgres, and
`tests/integration/db/` is where that is proven. These tests cover what the
constraint cannot: which HTTP answer each refusal produces, and that a refusal
happens before the database is asked at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationNotFoundError,
    PermissionDeniedError,
    ProviderCredentialNotFoundError,
    ProviderCredentialUnusableError,
)
from iam_platform.application.ai_resources.manage_provider_credential import (
    MANAGE_CREDENTIALS_PERMISSION,
    SetModelCredential,
    SetModelCredentialCommand,
)
from iam_platform.domain.ai_resources.entities import (
    CredentialOwnerType,
    ModelConfiguration,
    ProviderCredential,
)
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_answer_question_cases import _seed

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ALLOWED = frozenset({MANAGE_CREDENTIALS_PERMISSION})


def _configuration(uow: FakeAiResourceUnitOfWork, *, granted_to: UUID) -> ModelConfiguration:
    configuration = ModelConfiguration(
        id=uuid4(),
        tenant_id=None,
        model_name="gpt-5.5",
        parameters={},
        token_budget_per_month=None,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.model_configurations.by_id[configuration.id] = configuration
    uow.model_configurations.grant(
        tenant_id=granted_to, model_configuration_id=configuration.id
    )
    return configuration


def _credential(
    uow: FakeAiResourceUnitOfWork, *, tenant_id: UUID | None, revoked: bool = False
) -> ProviderCredential:
    credential = ProviderCredential(
        id=uuid4(),
        owner_type=(
            CredentialOwnerType.TENANT if tenant_id else CredentialOwnerType.PLATFORM
        ),
        tenant_id=tenant_id,
        provider="openai",
        credential_ciphertext=b"gAAAA-ciphertext",
        key_hint="8AkA",
        created_by_user_id=uuid4(),
        created_at=NOW,
        revoked_at=NOW if revoked else None,
    )
    uow.provider_credentials.by_id[credential.id] = credential
    return credential


def _command(
    *, user_id: UUID, tenant_id: UUID, configuration_id: UUID, credential_id: UUID | None,
    permissions: frozenset[str] = ALLOWED,
) -> SetModelCredentialCommand:
    return SetModelCredentialCommand(
        actor_user_id=str(user_id),
        tenant_id=str(tenant_id),
        model_configuration_id=str(configuration_id),
        permissions=permissions,
        provider_credential_id=str(credential_id) if credential_id else None,
    )


class TestSetModelCredential:
    async def test_attaching_records_the_credential_on_the_grant(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, _kb = _seed(uow)
        configuration = _configuration(uow, granted_to=tenant_id)
        credential = _credential(uow, tenant_id=tenant_id)

        await SetModelCredential(lambda _u, _t: uow).execute(  # type: ignore[arg-type]
            _command(
                user_id=user_id,
                tenant_id=tenant_id,
                configuration_id=configuration.id,
                credential_id=credential.id,
            )
        )

        assert await uow.model_configurations.credential_for_tenant(
            tenant_id=tenant_id, model_configuration_id=configuration.id
        ) == credential.id

    async def test_detaching_returns_the_model_to_the_platform_key(self) -> None:
        """An explicit null, not a missing field: "stop paying for this
        yourself" has to be expressible, or a tenant who attaches a key can
        never take it back without support."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, _kb = _seed(uow)
        configuration = _configuration(uow, granted_to=tenant_id)
        credential = _credential(uow, tenant_id=tenant_id)
        uow.model_configurations.grant_credentials[(tenant_id, configuration.id)] = (
            credential.id
        )

        await SetModelCredential(lambda _u, _t: uow).execute(  # type: ignore[arg-type]
            _command(
                user_id=user_id,
                tenant_id=tenant_id,
                configuration_id=configuration.id,
                credential_id=None,
            )
        )

        assert await uow.model_configurations.credential_for_tenant(
            tenant_id=tenant_id, model_configuration_id=configuration.id
        ) is None

    async def test_without_the_permission_it_is_refused(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, _kb = _seed(uow)
        configuration = _configuration(uow, granted_to=tenant_id)
        credential = _credential(uow, tenant_id=tenant_id)

        with pytest.raises(PermissionDeniedError):
            await SetModelCredential(lambda _u, _t: uow).execute(  # type: ignore[arg-type]
                _command(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    configuration_id=configuration.id,
                    credential_id=credential.id,
                    permissions=frozenset(),
                )
            )

        assert not uow.model_configurations.grant_credentials

    async def test_a_credential_this_tenant_cannot_see_is_not_found(self) -> None:
        """A platform-owned credential, or another tenant's. Reported as *not
        found* rather than *forbidden*, so neither is provable to exist -- and
        the composite FK would refuse it in the database even if this check
        were removed."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, _kb = _seed(uow)
        configuration = _configuration(uow, granted_to=tenant_id)
        platform_credential = _credential(uow, tenant_id=None)

        with pytest.raises(ProviderCredentialNotFoundError):
            await SetModelCredential(lambda _u, _t: uow).execute(  # type: ignore[arg-type]
                _command(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    configuration_id=configuration.id,
                    credential_id=platform_credential.id,
                )
            )

        assert not uow.model_configurations.grant_credentials

    async def test_a_revoked_credential_cannot_be_attached(self) -> None:
        """Refused here rather than at the next question. Attaching a revoked
        key produces a model that fails on its first use, discovered by a
        customer instead of by the person making the change."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, _kb = _seed(uow)
        configuration = _configuration(uow, granted_to=tenant_id)
        credential = _credential(uow, tenant_id=tenant_id, revoked=True)

        with pytest.raises(ProviderCredentialUnusableError):
            await SetModelCredential(lambda _u, _t: uow).execute(  # type: ignore[arg-type]
                _command(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    configuration_id=configuration.id,
                    credential_id=credential.id,
                )
            )

        assert not uow.model_configurations.grant_credentials

    async def test_a_model_this_tenant_was_never_granted_is_not_found(self) -> None:
        """Zero rows updated means no grant. Reported as *not found*, never as
        a failed write, so a configuration they are not entitled to stays
        unprovable -- the same rule the assistant path follows."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, _kb = _seed(uow)
        ungranted = _configuration(uow, granted_to=uuid4())
        credential = _credential(uow, tenant_id=tenant_id)

        with pytest.raises(ModelConfigurationNotFoundError):
            await SetModelCredential(lambda _u, _t: uow).execute(  # type: ignore[arg-type]
                _command(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    configuration_id=ungranted.id,
                    credential_id=credential.id,
                )
            )
