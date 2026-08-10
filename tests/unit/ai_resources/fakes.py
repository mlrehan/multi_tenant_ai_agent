"""In-memory fakes for the AI-resource ports.

Same shared-instance-as-factory + rollback-simulation pattern as
``tests/unit/identity/fakes.py`` and ``tests/unit/tenant_authz/fakes.py`` --
see the former's ``FakeIdentityUnitOfWork`` docstring for why the fake models
real transaction rollback rather than being a no-op.
"""

from __future__ import annotations

import copy
from datetime import datetime
from uuid import UUID

from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantMember,
    AssistantStatus,
    ChatWidget,
    Conversation,
    Document,
    KnowledgeBase,
    ModelConfiguration,
    ProviderCredential,
)
from tests.unit.tenant_authz.fakes import (
    FakeAuditWriter,
    FakeSecurityEventWriter,
    FakeTenantMembershipRepository,
)


class FakeAiAssistantRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, AiAssistant] = {}

    async def get_by_id(self, assistant_id: UUID) -> AiAssistant | None:
        return self.by_id.get(assistant_id)

    async def list_by_tenant(self, tenant_id: UUID) -> list[AiAssistant]:
        return [
            a
            for a in self.by_id.values()
            if a.tenant_id == tenant_id and a.status != AssistantStatus.ARCHIVED
        ]

    async def add(self, assistant: AiAssistant) -> None:
        self.by_id[assistant.id] = assistant

    async def save(self, assistant: AiAssistant) -> None:
        self.by_id[assistant.id] = assistant


class FakeAssistantMemberRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, AssistantMember] = {}

    async def get(self, *, assistant_id: UUID, membership_id: UUID) -> AssistantMember | None:
        return next(
            (
                m
                for m in self.by_id.values()
                if m.assistant_id == assistant_id and m.membership_id == membership_id
            ),
            None,
        )

    async def list_by_assistant(self, assistant_id: UUID) -> list[AssistantMember]:
        return [m for m in self.by_id.values() if m.assistant_id == assistant_id]

    async def list_for_membership(self, membership_id: UUID) -> list[AssistantMember]:
        return [m for m in self.by_id.values() if m.membership_id == membership_id]

    async def add(self, member: AssistantMember) -> None:
        self.by_id[member.id] = member

    async def remove(self, *, assistant_id: UUID, membership_id: UUID) -> None:
        existing = await self.get(assistant_id=assistant_id, membership_id=membership_id)
        if existing is not None:
            del self.by_id[existing.id]


class FakeKnowledgeBaseRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, KnowledgeBase] = {}

    async def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return self.by_id.get(knowledge_base_id)

    async def list_by_tenant(self, tenant_id: UUID) -> list[KnowledgeBase]:
        return [kb for kb in self.by_id.values() if kb.tenant_id == tenant_id]

    async def add(self, knowledge_base: KnowledgeBase) -> None:
        self.by_id[knowledge_base.id] = knowledge_base

    async def save(self, knowledge_base: KnowledgeBase) -> None:
        self.by_id[knowledge_base.id] = knowledge_base


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, Document] = {}
        #: Chunk counts per document. A count rather than the rows themselves:
        #: nothing in the application reads chunk content through this
        #: repository, so storing bodies would be fidelity the tests never use.
        self.chunks: dict[UUID, int] = {}

    async def get_by_id(self, document_id: UUID) -> Document | None:
        return self.by_id.get(document_id)

    async def list_by_knowledge_base(self, knowledge_base_id: UUID) -> list[Document]:
        return [
            d
            for d in self.by_id.values()
            if d.knowledge_base_id == knowledge_base_id and not d.is_deleted
        ]

    async def add(self, document: Document) -> None:
        self.by_id[document.id] = document

    async def save(self, document: Document) -> None:
        self.by_id[document.id] = document

    async def delete_chunks(self, document_id: UUID) -> int:
        removed = self.chunks.pop(document_id, 0)
        return removed

    async def count_chunks(self, document_id: UUID) -> int:
        return self.chunks.get(document_id, 0)


class FakeConversationRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, Conversation] = {}

    async def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        return self.by_id.get(conversation_id)

    async def list_by_membership(self, membership_id: UUID) -> list[Conversation]:
        return [c for c in self.by_id.values() if c.membership_id == membership_id]

    async def add(self, conversation: Conversation) -> None:
        self.by_id[conversation.id] = conversation

    async def save(self, conversation: Conversation) -> None:
        self.by_id[conversation.id] = conversation


class FakeModelConfigurationRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, ModelConfiguration] = {}

    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None:
        return self.by_id.get(model_configuration_id)

    async def list_available_to_tenant(self, tenant_id: UUID) -> list[ModelConfiguration]:
        return [
            m for m in self.by_id.values() if m.tenant_id is None or m.tenant_id == tenant_id
        ]

    async def add(self, model_configuration: ModelConfiguration) -> None:
        self.by_id[model_configuration.id] = model_configuration


class FakeProviderCredentialRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, ProviderCredential] = {}

    async def get_by_id(self, credential_id: UUID) -> ProviderCredential | None:
        return self.by_id.get(credential_id)

    async def list_by_tenant(self, tenant_id: UUID) -> list[ProviderCredential]:
        return [c for c in self.by_id.values() if c.tenant_id == tenant_id]

    async def add(self, credential: ProviderCredential) -> None:
        self.by_id[credential.id] = credential

    async def save(self, credential: ProviderCredential) -> None:
        self.by_id[credential.id] = credential


class FakeCredentialEncryptor:
    """Reversible, obviously-fake transform -- the tests care that plaintext
    never survives into a response, not that the cipher is strong."""

    def encrypt(self, plaintext: str) -> bytes:
        return f"enc:{plaintext}".encode()

    def decrypt(self, ciphertext: bytes) -> str:
        return ciphertext.decode().removeprefix("enc:")

    def key_hint(self, plaintext: str) -> str:
        return plaintext[-4:] if len(plaintext) > 4 else "*" * len(plaintext)


class FakeVectorNamespaceFactory:
    def build(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> str:
        return f"{tenant_id}/{knowledge_base_id}"


class FakeStoragePathFactory:
    def build(self, *, tenant_id: UUID, knowledge_base_id: UUID, document_id: UUID) -> str:
        return f"{tenant_id}/{knowledge_base_id}/{document_id}"


class FakeVectorSearchClient:
    def __init__(self) -> None:
        self.by_namespace: dict[str, list[tuple[UUID, float]]] = {}
        self.queried_namespaces: list[str] = []
        self.deleted: list[tuple[str, UUID]] = []

    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]:
        # Recorded so tests can assert *which* namespace was searched -- the
        # server-derived-namespace guarantee is only meaningful if the value
        # actually reaching the client is the stored one.
        self.queried_namespaces.append(namespace)
        return self.by_namespace.get(namespace, [])[:top_k]

    async def delete_document(self, *, namespace: str, document_id: UUID) -> None:
        # Recorded with the namespace, not just the id: a delete sent to the
        # wrong namespace would remove nothing and leave the document
        # answering queries, which is the failure this fake exists to catch.
        self.deleted.append((namespace, document_id))


class FakeDocumentIngestionQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, UUID]] = []
        #: Recorded separately so a test can assert the *actor* is carried --
        #: the worker re-validates that identity, so an enqueue that omitted
        #: it would make per-job authorization impossible.
        self.enqueued_actors: list[UUID] = []

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, document_id: UUID, at: datetime
    ) -> None:
        self.enqueued.append((tenant_id, document_id))
        self.enqueued_actors.append(actor_user_id)


class FakeObjectStorageClient:
    """In-memory object storage, keyed by path.

    Faithful enough to assert the real property: that the bytes reaching
    storage are the bytes uploaded, at the server-derived path.
    """

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, *, path: str, data: bytes, content_type: str) -> None:
        self.objects[path] = (data, content_type)

    async def get(self, *, path: str) -> bytes:
        from iam_platform.application.ai_resources.exceptions import (
            DocumentContentNotFoundError,
        )

        if path not in self.objects:
            raise DocumentContentNotFoundError(path)
        return self.objects[path][0]

    async def delete(self, *, path: str) -> None:
        self.objects.pop(path, None)


class FakeChatWidgetRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, ChatWidget] = {}

    async def add(self, widget: ChatWidget) -> None:
        self.by_id[widget.id] = widget

    async def list_for_tenant(self, tenant_id: UUID) -> list[ChatWidget]:
        return [w for w in self.by_id.values() if w.tenant_id == tenant_id]

    async def get_for_tenant(self, tenant_id: UUID, widget_id: UUID) -> ChatWidget | None:
        # Both predicates, matching the real repository. A fake that filtered
        # on id alone would let a cross-tenant test pass while the production
        # query was wrong.
        widget = self.by_id.get(widget_id)
        return widget if widget is not None and widget.tenant_id == tenant_id else None

    async def update(self, widget: ChatWidget) -> None:
        self.by_id[widget.id] = widget


class FakeAiResourceUnitOfWork:
    _REPO_ATTRS = (
        "tenant_memberships",
        "assistants",
        "assistant_members",
        "knowledge_bases",
        "documents",
        "chat_widgets",
        "conversations",
        "model_configurations",
        "provider_credentials",
        "audit",
        "security_events",
    )

    def __init__(self) -> None:
        self.tenant_memberships = FakeTenantMembershipRepository()
        self.assistants = FakeAiAssistantRepository()
        self.assistant_members = FakeAssistantMemberRepository()
        self.knowledge_bases = FakeKnowledgeBaseRepository()
        self.documents = FakeDocumentRepository()
        self.chat_widgets = FakeChatWidgetRepository()
        self.conversations = FakeConversationRepository()
        self.model_configurations = FakeModelConfigurationRepository()
        self.provider_credentials = FakeProviderCredentialRepository()
        self.audit = FakeAuditWriter()
        self.security_events = FakeSecurityEventWriter()
        self.last_user_id: UUID | None = None
        self.last_tenant_id: UUID | None = None
        self._snapshot: dict[str, object] | None = None

    def __call__(self, user_id: UUID, tenant_id: UUID) -> FakeAiResourceUnitOfWork:
        self.last_user_id = user_id
        self.last_tenant_id = tenant_id
        return self

    async def __aenter__(self) -> FakeAiResourceUnitOfWork:
        self._snapshot = {name: copy.deepcopy(getattr(self, name)) for name in self._REPO_ATTRS}
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            assert self._snapshot is not None
            for name, value in self._snapshot.items():
                setattr(self, name, value)
        self._snapshot = None
