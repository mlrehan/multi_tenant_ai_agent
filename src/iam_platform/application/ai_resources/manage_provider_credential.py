"""Tenant-scoped provider credentials -- docs/16-schema-ai-resources.md.

**The secret boundary:** plaintext enters through ``StoreProviderCredential``
and is immediately envelope-encrypted; it is never stored, logged, echoed, or
returned. Every read path in this module returns ``ProviderCredentialSummary``
-- a struct that structurally *cannot* carry ciphertext, since it has no field
for it. That's deliberate: a DTO that simply "doesn't populate" a secret field
is one careless edit away from populating it, whereas one with no such field
cannot leak by accident.

Decryption belongs to the AI-execution infrastructure at model-call time and
is not reachable from any use case here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.entitlements import guard_capability
from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationNotFoundError,
    PermissionDeniedError,
    ProviderCredentialNotFoundError,
    ProviderCredentialUnusableError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    CredentialEncryptor,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import CredentialOwnerType, ProviderCredential

MANAGE_CREDENTIALS_PERMISSION = "tenant.provider_credentials.manage"


@dataclass(frozen=True, slots=True)
class ProviderCredentialSummary:
    """Everything a UI may see about a credential. No ciphertext field exists
    on this type, by design -- see the module docstring."""

    id: UUID
    provider: str
    key_hint: str
    created_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


def _summarize(credential: ProviderCredential) -> ProviderCredentialSummary:
    return ProviderCredentialSummary(
        id=credential.id,
        provider=credential.provider,
        key_hint=credential.key_hint,
        created_at=credential.created_at,
        rotated_at=credential.rotated_at,
        revoked_at=credential.revoked_at,
    )


@dataclass(frozen=True, slots=True)
class StoreProviderCredentialCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    provider: str
    secret: str


class StoreProviderCredential:
    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        encryptor: CredentialEncryptor,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._encryptor = encryptor
        self._clock = clock

    async def execute(self, command: StoreProviderCredentialCommand) -> ProviderCredentialSummary:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CREDENTIALS_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CREDENTIALS_PERMISSION)

            # BYOK is a plan feature: a tenant that may not bring its own key
            # cannot store one either, or it would sit there looking active
            # while the platform kept paying the bill.
            await guard_capability(
                uow,
                tenant_id=tenant_id,
                clock=self._clock,
                capability="allow_own_provider_credentials",
            )

            credential = ProviderCredential(
                id=uuid4(),
                owner_type=CredentialOwnerType.TENANT,
                tenant_id=tenant_id,
                provider=command.provider,
                credential_ciphertext=self._encryptor.encrypt(command.secret),
                key_hint=self._encryptor.key_hint(command.secret),
                created_by_user_id=actor_id,
                created_at=now,
            )
            await uow.provider_credentials.add(credential)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.provider_credential_created",
                resource_type="provider_credential",
                resource_id=credential.id,
                result="success",
                # provider + key_hint only -- never the secret, per
                # docs/03-threat-model.md's "never log tokens/secrets".
                metadata={"provider": command.provider, "key_hint": credential.key_hint},
            )
            return _summarize(credential)


@dataclass(frozen=True, slots=True)
class ListProviderCredentialsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class ListProviderCredentials:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListProviderCredentialsQuery) -> list[ProviderCredentialSummary]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CREDENTIALS_PERMISSION not in query.permissions:
                raise PermissionDeniedError(MANAGE_CREDENTIALS_PERMISSION)
            return [
                _summarize(c) for c in await uow.provider_credentials.list_by_tenant(tenant_id)
            ]


@dataclass(frozen=True, slots=True)
class RotateProviderCredentialCommand:
    actor_user_id: str
    tenant_id: str
    credential_id: str
    permissions: frozenset[str]
    new_secret: str


class RotateProviderCredential:
    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        encryptor: CredentialEncryptor,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._encryptor = encryptor
        self._clock = clock

    async def execute(self, command: RotateProviderCredentialCommand) -> ProviderCredentialSummary:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CREDENTIALS_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CREDENTIALS_PERMISSION)

            credential = await uow.provider_credentials.get_by_id(UUID(command.credential_id))
            if credential is None:
                raise ProviderCredentialNotFoundError(command.credential_id)

            credential.rotate(
                ciphertext=self._encryptor.encrypt(command.new_secret),
                key_hint=self._encryptor.key_hint(command.new_secret),
                now=now,
            )
            await uow.provider_credentials.save(credential)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.provider_credential_rotated",
                resource_type="provider_credential",
                resource_id=credential.id,
                result="success",
                metadata={"provider": credential.provider, "key_hint": credential.key_hint},
            )
            return _summarize(credential)


@dataclass(frozen=True, slots=True)
class RevokeProviderCredentialCommand:
    actor_user_id: str
    tenant_id: str
    credential_id: str
    permissions: frozenset[str]


class RevokeProviderCredential:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RevokeProviderCredentialCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CREDENTIALS_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CREDENTIALS_PERMISSION)

            credential = await uow.provider_credentials.get_by_id(UUID(command.credential_id))
            if credential is None:
                raise ProviderCredentialNotFoundError(command.credential_id)
            if not credential.is_active:
                return  # idempotent

            credential.revoke(now=now)
            await uow.provider_credentials.save(credential)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.provider_credential_revoked",
                resource_type="provider_credential",
                resource_id=credential.id,
                result="success",
                metadata={"provider": credential.provider},
            )


@dataclass(frozen=True, slots=True)
class SetModelCredentialCommand:
    actor_user_id: str
    tenant_id: str
    model_configuration_id: str
    permissions: frozenset[str]
    #: None detaches, returning this model to the platform's own key.
    provider_credential_id: str | None


class SetModelCredential:
    """Points one entitled model at this tenant's own provider key -- BYOK.

    **The attachment lives on the grant, not on the configuration**, because a
    configuration is platform-owned and granted to many tenants: a credential
    column on it could only ever name one key for everyone, which is the
    opposite of "bill me for my own questions". `answer_question.py`'s
    `_resolve_credential` reads it back from the same place.

    Three checks, and the database independently enforces the one that matters
    most:
    1. **`tenant.provider_credentials.manage`** -- the same permission that
       creates the credential. Deciding a key pays for something is the same
       authority as holding the key; a separate permission would imply the two
       could sensibly be held apart.
    2. **The credential must exist, be this tenant's, and not be revoked.**
       Attaching a revoked credential would produce a model that fails on its
       next question, discovered by a customer rather than here.
    3. **The tenant must actually hold the grant** -- a zero-row update is
       reported as *not found*, never as a failed write, so a configuration
       they were never granted stays unprovable.

    Cross-tenant attachment is refused by `fk_tenant_model_configurations_provider_credential`
    even if all three checks above were somehow bypassed: the FK is composite on
    `(tenant_id, provider_credential_id)` and `tenant_id` here is NOT NULL, so
    another tenant's credential -- and a platform-owned one -- cannot be
    referenced at all.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: SetModelCredentialCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        model_configuration_id = UUID(command.model_configuration_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_CREDENTIALS_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CREDENTIALS_PERMISSION)

            credential_id: UUID | None = None
            if command.provider_credential_id is not None:
                credential_id = UUID(command.provider_credential_id)
                # RLS scopes this read to the tenant, so another tenant's
                # credential is simply absent -- reported as *not found*, which
                # is also what a deleted one looks like. Neither is provable.
                credential = await uow.provider_credentials.get_by_id(credential_id)
                if credential is None or credential.tenant_id != tenant_id:
                    raise ProviderCredentialNotFoundError(str(credential_id))
                if not credential.is_active:
                    raise ProviderCredentialUnusableError(
                        "this provider credential has been revoked and cannot be "
                        "attached to a model"
                    )

            matched = await uow.model_configurations.set_credential_for_tenant(
                tenant_id=tenant_id,
                model_configuration_id=model_configuration_id,
                provider_credential_id=credential_id,
            )
            if matched == 0:
                raise ModelConfigurationNotFoundError(str(model_configuration_id))
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.model_credential_set",
                resource_type="model_configuration",
                resource_id=model_configuration_id,
                result="success",
                # Which credential, never any part of it. Recorded because
                # this is the moment the payer for a model changes hands.
                metadata={
                    "provider_credential_id": (
                        str(credential_id) if credential_id else None
                    )
                },
            )
