"""The two structural guarantees this phase exists to prove:

1. ``vector_namespace``/``storage_path`` are server-derived and never
   client-suppliable, so a vector query can only ever reach the namespace of a
   knowledge base the caller was already authorized for (Phase 1 §12).
2. Provider-credential plaintext never survives into anything a read path
   returns (docs/16-schema-ai-resources.md).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
    ResourceAccessDeniedError,
)
from iam_platform.application.ai_resources.manage_knowledge_base import (
    CreateKnowledgeBase,
    CreateKnowledgeBaseCommand,
    QueryKnowledgeBase,
    QueryKnowledgeBaseQuery,
    UploadDocument,
    UploadDocumentCommand,
)
from iam_platform.application.ai_resources.manage_provider_credential import (
    ListProviderCredentials,
    ListProviderCredentialsQuery,
    ProviderCredentialSummary,
    RevokeProviderCredential,
    RevokeProviderCredentialCommand,
    RotateProviderCredential,
    RotateProviderCredentialCommand,
    StoreProviderCredential,
    StoreProviderCredentialCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    ResourceVisibility,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.ai_resources.fakes import (
    FakeAiResourceUnitOfWork,
    FakeCredentialEncryptor,
    FakeDocumentIngestionQueue,
    FakeObjectStorageClient,
    FakeStoragePathFactory,
    FakeVectorNamespaceFactory,
    FakeVectorSearchClient,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)

CREATE_KB = "tenant.knowledge_bases.create"
UPLOAD = "tenant.documents.upload"
QUERY = "tenant.knowledge_bases.query"
MANAGE_CREDENTIALS = "tenant.provider_credentials.manage"


def _seed_member(uow: FakeAiResourceUnitOfWork, tenant_id):
    user_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    return user_id, membership


def _seed_knowledge_base(
    uow: FakeAiResourceUnitOfWork,
    tenant_id,
    owner_membership_id,
    *,
    visibility=ResourceVisibility.TENANT,
) -> KnowledgeBase:
    kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_id,
        name="kb",
        owner_membership_id=owner_membership_id,
        visibility=visibility,
        vector_namespace=f"{tenant_id}/{uuid4()}",
        created_at=NOW,
        updated_at=NOW,
    )
    uow.knowledge_bases.by_id[kb.id] = kb
    return kb


class TestServerDerivedNamespaceAndPath:
    def test_create_command_has_no_vector_namespace_field(self) -> None:
        """Structural, not behavioural: there is no field a client could set."""
        fields = {f.name for f in dataclasses.fields(CreateKnowledgeBaseCommand)}
        assert "vector_namespace" not in fields

    def test_upload_command_has_no_storage_path_field(self) -> None:
        fields = {f.name for f in dataclasses.fields(UploadDocumentCommand)}
        assert "storage_path" not in fields

    async def test_namespace_is_derived_from_tenant_and_kb_ids(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        use_case = CreateKnowledgeBase(uow, FakeVectorNamespaceFactory(), FixedClock(NOW))
        kb_id = await use_case.execute(
            CreateKnowledgeBaseCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({CREATE_KB}),
                name="Docs",
            )
        )
        kb = await uow.knowledge_bases.get_by_id(kb_id)
        assert kb is not None
        assert kb.vector_namespace == f"{tenant_id}/{kb_id}"

    async def test_storage_path_is_derived_from_tenant_kb_and_document_ids(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        queue = FakeDocumentIngestionQueue()

        storage = FakeObjectStorageClient()
        use_case = UploadDocument(
            uow, FakeStoragePathFactory(), queue, FixedClock(NOW), storage
        )
        document_id = await use_case.execute(
            UploadDocumentCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                permissions=frozenset({UPLOAD}),
                filename="handbook.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                checksum="abc123",
                content=b"file bytes",
            )
        )
        document = await uow.documents.get_by_id(document_id)
        assert document is not None
        assert document.storage_path == f"{tenant_id}/{kb.id}/{document_id}"
        assert document.status == DocumentStatus.PROCESSING
        assert queue.enqueued == [(tenant_id, document_id)]

    async def test_query_searches_the_stored_namespace_only(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)

        document = Document(
            id=uuid4(),
            tenant_id=tenant_id,
            knowledge_base_id=kb.id,
            uploaded_by_membership_id=membership.id,
            filename="handbook.pdf",
            content_type="application/pdf",
            storage_path=f"{tenant_id}/{kb.id}/x",
            size_bytes=10,
            status=DocumentStatus.READY,
            checksum="c",
            created_at=NOW,
        )
        uow.documents.by_id[document.id] = document

        search = FakeVectorSearchClient()
        search.by_namespace[kb.vector_namespace] = [(document.id, 0.9)]

        use_case = QueryKnowledgeBase(uow, search)
        hits = await use_case.execute(
            QueryKnowledgeBaseQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                permissions=frozenset({QUERY}),
                query_text="vacation policy",
            )
        )
        assert search.queried_namespaces == [kb.vector_namespace]
        assert [h.document_id for h in hits] == [document.id]

    async def test_query_on_an_invisible_knowledge_base_never_reaches_the_search_client(
        self,
    ) -> None:
        """The authorization check runs *before* any search, so an unauthorized
        knowledge_base_id cannot cause a query against its namespace."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(
            uow, tenant_id, other_membership.id, visibility=ResourceVisibility.RESTRICTED
        )
        search = FakeVectorSearchClient()

        use_case = QueryKnowledgeBase(uow, search)
        with pytest.raises(KnowledgeBaseNotFoundError):
            await use_case.execute(
                QueryKnowledgeBaseQuery(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    knowledge_base_id=str(kb.id),
                    permissions=frozenset({QUERY}),
                    query_text="secrets",
                )
            )
        assert search.queried_namespaces == []

    async def test_search_hits_for_deleted_documents_are_dropped(self) -> None:
        """The database wins over stale vector-store metadata."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)

        stale_document_id = uuid4()
        search = FakeVectorSearchClient()
        search.by_namespace[kb.vector_namespace] = [(stale_document_id, 0.99)]

        use_case = QueryKnowledgeBase(uow, search)
        hits = await use_case.execute(
            QueryKnowledgeBaseQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                permissions=frozenset({QUERY}),
                query_text="anything",
            )
        )
        assert hits == []

    async def test_upload_requires_modify_rights_not_just_read(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        _, other_membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, other_membership.id)

        use_case = UploadDocument(
            uow,
            FakeStoragePathFactory(),
            FakeDocumentIngestionQueue(),
            FixedClock(NOW),
            FakeObjectStorageClient(),
        )
        with pytest.raises(ResourceAccessDeniedError):
            await use_case.execute(
                UploadDocumentCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    knowledge_base_id=str(kb.id),
                    permissions=frozenset({UPLOAD}),
                    filename="x.pdf",
                    content_type="application/pdf",
                    size_bytes=1,
                    checksum="c",
                    content=b"file bytes",
                )
            )
        assert uow.documents.by_id == {}


class TestProviderCredentialSecretBoundary:
    def test_summary_type_has_no_ciphertext_field(self) -> None:
        """Structural guarantee: the read DTO cannot carry a secret, so no
        careless edit can populate one."""
        fields = {f.name for f in dataclasses.fields(ProviderCredentialSummary)}
        assert "credential_ciphertext" not in fields
        assert "secret" not in fields
        assert fields == {
            "id",
            "provider",
            "key_hint",
            "created_at",
            "rotated_at",
            "revoked_at",
        }

    async def test_store_returns_a_summary_without_the_secret(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        use_case = StoreProviderCredential(uow, FakeCredentialEncryptor(), FixedClock(NOW))
        summary = await use_case.execute(
            StoreProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                provider="anthropic",
                secret="sk-super-secret-value",
            )
        )
        assert summary.key_hint == "alue"
        assert "sk-super-secret-value" not in repr(summary)

    async def test_secret_is_stored_encrypted_not_plaintext(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        use_case = StoreProviderCredential(uow, FakeCredentialEncryptor(), FixedClock(NOW))
        summary = await use_case.execute(
            StoreProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                provider="anthropic",
                secret="sk-super-secret-value",
            )
        )
        stored = await uow.provider_credentials.get_by_id(summary.id)
        assert stored is not None
        assert stored.credential_ciphertext != b"sk-super-secret-value"
        assert b"enc:" in stored.credential_ciphertext

    async def test_audit_record_never_contains_the_secret(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        use_case = StoreProviderCredential(uow, FakeCredentialEncryptor(), FixedClock(NOW))
        await use_case.execute(
            StoreProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                provider="anthropic",
                secret="sk-super-secret-value",
            )
        )
        assert "sk-super-secret-value" not in str(uow.audit.events)

    async def test_list_returns_summaries_only(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        await StoreProviderCredential(uow, FakeCredentialEncryptor(), FixedClock(NOW)).execute(
            StoreProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                provider="anthropic",
                secret="sk-super-secret-value",
            )
        )

        summaries = await ListProviderCredentials(uow).execute(
            ListProviderCredentialsQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
            )
        )
        assert len(summaries) == 1
        assert all(isinstance(s, ProviderCredentialSummary) for s in summaries)
        assert "sk-super-secret-value" not in str(summaries)

    async def test_rotation_replaces_ciphertext_and_hint(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)
        encryptor = FakeCredentialEncryptor()

        created = await StoreProviderCredential(uow, encryptor, FixedClock(NOW)).execute(
            StoreProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                provider="anthropic",
                secret="old-secret-aaaa",
            )
        )
        rotated = await RotateProviderCredential(uow, encryptor, FixedClock(NOW)).execute(
            RotateProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                credential_id=str(created.id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                new_secret="new-secret-bbbb",
            )
        )
        assert rotated.key_hint == "bbbb"
        assert rotated.rotated_at == NOW
        stored = await uow.provider_credentials.get_by_id(created.id)
        assert stored is not None
        assert stored.credential_ciphertext == b"enc:new-secret-bbbb"

    async def test_revocation_is_idempotent(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        created = await StoreProviderCredential(
            uow, FakeCredentialEncryptor(), FixedClock(NOW)
        ).execute(
            StoreProviderCredentialCommand(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                permissions=frozenset({MANAGE_CREDENTIALS}),
                provider="anthropic",
                secret="secret-value",
            )
        )
        use_case = RevokeProviderCredential(uow, FixedClock(NOW))
        command = RevokeProviderCredentialCommand(
            actor_user_id=str(user_id),
            tenant_id=str(tenant_id),
            credential_id=str(created.id),
            permissions=frozenset({MANAGE_CREDENTIALS}),
        )
        await use_case.execute(command)
        await use_case.execute(command)

        stored = await uow.provider_credentials.get_by_id(created.id)
        assert stored is not None
        assert stored.revoked_at == NOW
        revocation_events = [
            e
            for e in uow.audit.events
            if e["action"] == "ai_resources.provider_credential_revoked"
        ]
        assert len(revocation_events) == 1

    async def test_denied_without_manage_permission(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, _ = _seed_member(uow, tenant_id)

        use_case = StoreProviderCredential(uow, FakeCredentialEncryptor(), FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                StoreProviderCredentialCommand(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    permissions=frozenset(),
                    provider="anthropic",
                    secret="sk-secret",
                )
            )
        assert uow.provider_credentials.by_id == {}
