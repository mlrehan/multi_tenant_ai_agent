"""`provider_credential_id` (BYOK), wired.

Stored, encrypted, entitlement-checked and rendered in the console since Phase
7 -- and read by nothing, exactly like `token_budget_per_month` before it. A
tenant who pasted their own OpenAI key was billing the platform and had no way
to find that out.

Each test names the failure it prevents. The one that matters most is
`test_a_revoked_credential_refuses_rather_than_billing_the_platform`: falling
back to the platform's key is the *tempting* behaviour, because answers keep
working.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ProviderCredentialUnusableError,
)
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantStatus,
    CredentialOwnerType,
    ModelConfiguration,
    ProviderCredential,
    ResourceVisibility,
)
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_answer_question import (
    _chunk,
    _FakeChatModel,
    _FakeVectorSearch,
)
from tests.unit.ai_resources.test_answer_question_cases import _build, _query, _seed

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CIPHERTEXT = b"gAAAAA-pretend-this-is-a-fernet-token"


class _CredentialRecordingChatModel(_FakeChatModel):
    """Records what the adapter was handed, because "used the tenant's key" and
    "used the platform's" produce an identical answer -- the only observable
    difference is which account the request was billed to."""

    def __init__(self, reply: str = "An answer [1].") -> None:
        super().__init__(reply=reply)
        self.ciphertexts: list[bytes | None] = []

    def stream_answer(self, **kwargs: object):  # type: ignore[no-untyped-def,override]
        self.ciphertexts.append(kwargs.pop("credential_ciphertext", None))  # type: ignore[arg-type]
        kwargs.pop("usage", None)
        return super().stream_answer(**kwargs)  # type: ignore[arg-type]


def _seed_credentialled_assistant(
    uow: FakeAiResourceUnitOfWork,
    *,
    tenant_id: UUID,
    membership_id: UUID,
    credential: ProviderCredential | None,
    dangling_credential_id: UUID | None = None,
) -> AiAssistant:
    """`dangling_credential_id` attaches a credential that was never stored --
    what a caller sees when RLS hides another tenant's row, which is the case
    that must not silently degrade."""
    if credential is not None:
        uow.provider_credentials.by_id[credential.id] = credential
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
        tenant_id=tenant_id, model_configuration_id=configuration.id
    )
    # Attached to the *grant*, which is where the real column lives.
    attached = dangling_credential_id or (credential.id if credential else None)
    if attached is not None:
        uow.model_configurations.grant_credentials[(tenant_id, configuration.id)] = attached
    assistant = AiAssistant(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Support Bot",
        owner_membership_id=membership_id,
        visibility=ResourceVisibility.TENANT,
        model_configuration_id=configuration.id,
        status=AssistantStatus.PUBLISHED,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.assistants.by_id[assistant.id] = assistant
    return assistant


def _credential(tenant_id: UUID, *, revoked: bool = False) -> ProviderCredential:
    return ProviderCredential(
        id=uuid4(),
        owner_type=CredentialOwnerType.TENANT,
        tenant_id=tenant_id,
        provider="openai",
        credential_ciphertext=CIPHERTEXT,
        key_hint="ab12",
        created_by_user_id=uuid4(),
        created_at=NOW,
        revoked_at=NOW if revoked else None,
    )


def _membership_id(uow: FakeAiResourceUnitOfWork) -> UUID:
    return next(iter(uow.tenant_memberships.by_id))


class TestByokCredentials:
    async def test_a_configured_credential_reaches_the_model_call(self) -> None:
        """The whole point: a tenant who supplied their own key is billed on
        their own provider account, not the platform's."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_credentialled_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            credential=_credential(tenant_id),
        )
        chat = _CredentialRecordingChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat)

        result = await use_case.execute(
            _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
        )
        [token async for token in result.tokens]

        assert chat.ciphertexts == [CIPHERTEXT]

    async def test_the_plaintext_key_never_crosses_the_application_boundary(
        self,
    ) -> None:
        """`CredentialEncryptor`'s contract is that only the AI-execution
        infrastructure decrypts. What the use case forwards must therefore be
        the stored ciphertext verbatim -- a use case that decrypted here would
        put a plaintext provider key into application-layer locals, log
        records and tracebacks."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        credential = _credential(tenant_id)
        assistant = _seed_credentialled_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            credential=credential,
        )
        chat = _CredentialRecordingChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat)

        result = await use_case.execute(
            _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
        )
        [token async for token in result.tokens]

        assert chat.ciphertexts == [credential.credential_ciphertext]

    async def test_a_revoked_credential_refuses_rather_than_billing_the_platform(
        self,
    ) -> None:
        """Revoking a credential must stop answers through it, not silently
        redirect the cost. A fallback would keep the console green while moving
        the bill, and nothing would report it."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_credentialled_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            credential=_credential(tenant_id, revoked=True),
        )
        chat = _CredentialRecordingChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat)

        with pytest.raises(ProviderCredentialUnusableError):
            await use_case.execute(
                _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
            )

        assert chat.calls == [], "the model must not be called on a revoked credential"

    async def test_an_invisible_credential_refuses_rather_than_falling_back(
        self,
    ) -> None:
        """What a tenant sees when the named credential belongs to someone else:
        RLS returns nothing. Indistinguishable here from "deleted", and both
        must refuse -- resolving to `None` and quietly using the platform key is
        the cross-tenant version of the same billing swap."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_credentialled_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            credential=None,
            dangling_credential_id=uuid4(),
        )
        chat = _CredentialRecordingChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat)

        with pytest.raises(ProviderCredentialUnusableError):
            await use_case.execute(
                _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
            )

        assert chat.calls == []

    async def test_a_configuration_with_no_credential_uses_the_platform_key(
        self,
    ) -> None:
        """Every configuration that exists today has `provider_credential_id`
        NULL, so this is the path the whole deployment is on. It must not
        acquire a credential requirement by accident."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_credentialled_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            credential=None,
        )
        chat = _CredentialRecordingChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat)

        result = await use_case.execute(
            _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
        )
        [token async for token in result.tokens]

        assert chat.ciphertexts == [None]

    async def test_a_platform_default_answer_is_unchanged(self) -> None:
        """No assistant means no configuration and so no credential -- the
        public widget's path, which this work must leave byte-for-byte alone."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        chat = _CredentialRecordingChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat)

        result = await use_case.execute(_query(tenant_id, user_id, kb, "Refunds?"))
        [token async for token in result.tokens]

        assert chat.ciphertexts == [None]
