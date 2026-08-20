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

from iam_platform.application.ai_resources.ports import StoredChunk
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantMember,
    AssistantStatus,
    ChatWidget,
    Conversation,
    ConversationMessage,
    DataSource,
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
        #: Chunk counts per document, kept separately from the rows below so a
        #: test can assert a count without having to build chunk bodies.
        self.chunks: dict[UUID, int] = {}
        #: Chunk bodies, for the tests that read them back. Only populated by
        #: tests that exercise the detail view.
        self.chunk_rows: dict[UUID, list[StoredChunk]] = {}

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
        self.chunk_rows.pop(document_id, None)
        return removed

    async def count_chunks(self, document_id: UUID) -> int:
        return self.chunks.get(document_id, 0)

    async def list_chunks(
        self, document_id: UUID, *, limit: int, offset: int
    ) -> list[StoredChunk]:
        rows = sorted(
            self.chunk_rows.get(document_id, []), key=lambda c: c.chunk_index
        )
        return rows[offset : offset + limit]


class FakeDataSourceRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, DataSource] = {}

    async def add(self, source: DataSource) -> None:
        self.by_id[source.id] = source

    async def save(self, source: DataSource) -> None:
        self.by_id[source.id] = source

    async def get(self, *, tenant_id: UUID, source_id: UUID) -> DataSource | None:
        source = self.by_id.get(source_id)
        # Mirrors the real repository's belt-and-braces tenant check, so a test
        # that passes a foreign id gets None here as it would in production.
        if source is None or source.tenant_id != tenant_id:
            return None
        return source

    async def list_for_knowledge_base(
        self, *, tenant_id: UUID, knowledge_base_id: UUID
    ) -> list[DataSource]:
        return [
            s
            for s in self.by_id.values()
            if s.tenant_id == tenant_id and s.knowledge_base_id == knowledge_base_id
        ]


class FakeCrawlJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, UUID]] = []
        self.enqueued_actors: list[UUID] = []

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, data_source_id: UUID, at: datetime
    ) -> None:
        self.enqueued.append((tenant_id, data_source_id))
        self.enqueued_actors.append(actor_user_id)


class FakeUrlValidator:
    """Records what it was asked about, and refuses anything in `unsafe`.

    Recording matters as much as refusing: the property under test is that a
    re-sync re-checks its stored URLs rather than trusting that they passed
    when the source was created.
    """

    def __init__(self, unsafe: set[str] | None = None) -> None:
        self.checked: list[str] = []
        self.unsafe = unsafe or set()

    def assert_safe(self, url: str) -> None:
        self.checked.append(url)
        if url in self.unsafe:
            from iam_platform.infrastructure.crawling.url_safety import (
                UnsafeCrawlTargetError,
            )

            raise UnsafeCrawlTargetError(f"refusing {url}")


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

    async def list_by_tenant(self, tenant_id: UUID) -> list[Conversation]:
        return [c for c in self.by_id.values() if c.tenant_id == tenant_id]

    async def list_by_ids(self, conversation_ids: list[UUID]) -> list[Conversation]:
        return [self.by_id[i] for i in conversation_ids if i in self.by_id]

    async def delete(self, conversation_id: UUID) -> None:
        self.by_id.pop(conversation_id, None)


class FakeConversationMessageRepository:
    """Append-only, like the real table -- there is deliberately no `save`.

    Cascade is simulated on the unit of work rather than here, because in
    Postgres it is the *conversation* delete that removes these rows.
    """

    def __init__(self) -> None:
        self.by_conversation: dict[UUID, list[ConversationMessage]] = {}

    async def add_many(self, messages: list[ConversationMessage]) -> None:
        for m in messages:
            self.by_conversation.setdefault(m.conversation_id, []).append(m)

    async def next_seq(self, conversation_id: UUID) -> int:
        existing = self.by_conversation.get(conversation_id, [])
        return (max((m.seq for m in existing), default=0)) + 1

    async def list_after(
        self, *, conversation_id: UUID, after_seq: int
    ) -> list[ConversationMessage]:
        return [
            m
            for m in sorted(
                self.by_conversation.get(conversation_id, []), key=lambda x: x.seq
            )
            if m.seq > after_seq
        ]

    async def list_page(
        self, *, conversation_id: UUID, limit: int, offset: int
    ) -> list[ConversationMessage]:
        ordered = sorted(self.by_conversation.get(conversation_id, []), key=lambda x: x.seq)
        return ordered[offset : offset + limit]

    async def list_tail(
        self, *, conversation_id: UUID, limit: int, before_seq: int | None = None
    ) -> list[ConversationMessage]:
        ordered = sorted(self.by_conversation.get(conversation_id, []), key=lambda x: x.seq)
        if before_seq is not None:
            ordered = [m for m in ordered if m.seq < before_seq]
        return ordered[-limit:] if limit > 0 else []

    async def count_for_conversation(self, conversation_id: UUID) -> int:
        return len(self.by_conversation.get(conversation_id, []))

    async def search(
        self, *, tenant_id: UUID, membership_id: UUID | None, text: str, limit: int
    ) -> list[UUID]:
        found: list[UUID] = []
        for conversation_id, messages in self.by_conversation.items():
            if any(
                m.tenant_id == tenant_id and text.lower() in m.content.lower()
                for m in messages
            ):
                found.append(conversation_id)
        return found[:limit]


class FakeModelConfigurationRepository:
    """Models entitlements, not ownership.

    `grants` is deliberately separate from `by_id`: a configuration existing
    and a tenant being allowed to use it are different facts, and a fake that
    conflated them would let a test pass while the real rule was broken --
    which is precisely the bug the entitlement table was introduced to fix.
    """

    def __init__(self) -> None:
        self.by_id: dict[UUID, ModelConfiguration] = {}
        self.grants: set[tuple[UUID, UUID]] = set()
        # Keyed by the same pair as `grants`, because a BYOK credential belongs
        # to the grant rather than to the shared configuration -- modelling it
        # on `by_id` would let a test pass while the real column could not
        # express whose key pays.
        self.grant_credentials: dict[tuple[UUID, UUID], UUID] = {}

    def grant(self, *, tenant_id: UUID, model_configuration_id: UUID) -> None:
        """Test helper -- the equivalent of a platform admin granting access."""
        self.grants.add((tenant_id, model_configuration_id))

    async def credential_for_tenant(
        self, *, tenant_id: UUID, model_configuration_id: UUID
    ) -> UUID | None:
        return self.grant_credentials.get((tenant_id, model_configuration_id))

    async def credentials_for_tenant(self, tenant_id: UUID) -> dict[UUID, UUID]:
        return {
            config_id: credential_id
            for (t, config_id), credential_id in self.grant_credentials.items()
            if t == tenant_id
        }

    async def set_credential_for_tenant(
        self,
        *,
        tenant_id: UUID,
        model_configuration_id: UUID,
        provider_credential_id: UUID | None,
    ) -> int:
        pair = (tenant_id, model_configuration_id)
        if pair not in self.grants:
            return 0
        if provider_credential_id is None:
            self.grant_credentials.pop(pair, None)
        else:
            self.grant_credentials[pair] = provider_credential_id
        return 1

    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None:
        return self.by_id.get(model_configuration_id)

    async def list_available_to_tenant(self, tenant_id: UUID) -> list[ModelConfiguration]:
        return [
            m
            for m in self.by_id.values()
            if (tenant_id, m.id) in self.grants and not m.is_archived
        ]

    async def is_available_to_tenant(
        self, *, tenant_id: UUID, model_configuration_id: UUID
    ) -> bool:
        configuration = self.by_id.get(model_configuration_id)
        return (
            configuration is not None
            and not configuration.is_archived
            and (tenant_id, model_configuration_id) in self.grants
        )

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




class FakeTenantEntitlementRepository:
    """Permissive by default -- see the module note in fix_fakes.

    Standing in for a generously provisioned tenant keeps tests that are not
    about plans from failing on a limit they never configured. A test about
    entitlements assigns `self.stored[tenant_id]`.
    """

    def __init__(self) -> None:
        self.stored: dict[UUID, object] = {}
        self.knowledge_bases = 0
        self.chat_widgets = 0
        self.assistants = 0

    async def get_for_tenant(self, tenant_id: UUID) -> object | None:
        if tenant_id in self.stored:
            return self.stored[tenant_id]
        from datetime import UTC, datetime
        from uuid import uuid4

        from iam_platform.domain.tenancy.entitlements import TenantEntitlements

        now = datetime(2026, 1, 1, tzinfo=UTC)
        return TenantEntitlements(
            id=uuid4(),
            tenant_id=tenant_id,
            max_knowledge_bases=None,
            max_chat_widgets=None,
            max_messages_per_day=None,
            max_tokens_per_month=None,
            allow_own_provider_credentials=True,
            allow_create_assistant=True,
            allow_invite_members=True,
            allow_create_roles=True,
            created_at=now,
            updated_at=now,
        )

    async def upsert(self, entitlements: object) -> None:
        self.stored[entitlements.tenant_id] = entitlements  # type: ignore[attr-defined]

    async def list_all(self) -> list[object]:
        return list(self.stored.values())

    async def count_knowledge_bases(self, tenant_id: UUID) -> int:
        return self.knowledge_bases

    async def count_chat_widgets(self, tenant_id: UUID) -> int:
        return self.chat_widgets

    async def count_assistants(self, tenant_id: UUID) -> int:
        return self.assistants


class FakeTenantChatbotSettingsRepository:
    def __init__(self) -> None:
        self.stored: dict[UUID, object] = {}

    async def get_for_tenant(self, tenant_id: UUID) -> object | None:
        return self.stored.get(tenant_id)

    async def upsert(self, settings: object) -> None:
        self.stored[settings.tenant_id] = settings  # type: ignore[attr-defined]


class FakeTenantTeamRepository:
    def __init__(self) -> None:
        self.teams: dict[UUID, object] = {}
        self.members: dict[UUID, list[UUID]] = {}

    async def get(self, *, tenant_id: UUID, team_id: UUID) -> object | None:
        team = self.teams.get(team_id)
        return team if team is not None and team.tenant_id == tenant_id else None  # type: ignore[attr-defined]

    async def list_for_tenant(
        self, tenant_id: UUID, *, active_only: bool = False
    ) -> list[object]:
        return [
            t
            for t in self.teams.values()
            if t.tenant_id == tenant_id and (not active_only or t.is_active)  # type: ignore[attr-defined]
        ]

    async def add(self, team: object) -> None:
        self.teams[team.id] = team  # type: ignore[attr-defined]

    async def save(self, team: object) -> None:
        self.teams[team.id] = team  # type: ignore[attr-defined]

    async def list_members(self, *, tenant_id: UUID, team_id: UUID) -> list[UUID]:
        return list(self.members.get(team_id, []))

    async def set_members(
        self, *, tenant_id: UUID, team_id: UUID, membership_ids: list[UUID]
    ) -> None:
        self.members[team_id] = list(membership_ids)

    async def teams_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> list[UUID]:
        return [t for t, ms in self.members.items() if membership_id in ms]


class FakeConversationHandoffRepository:
    """Simulates the conditional UPDATE, including its refusals.

    The production repository settles a two-agent race in Postgres; this fake
    reproduces the *observable* behaviour -- a second claim returns False --
    so a use-case test can assert the loser is told, without a database.
    """

    def __init__(self, conversations: object) -> None:
        self._conversations = conversations

    def _get(self, tenant_id: UUID, conversation_id: UUID) -> object | None:
        c = self._conversations.by_id.get(conversation_id)  # type: ignore[attr-defined]
        return c if c is not None and c.tenant_id == tenant_id else None  # type: ignore[attr-defined]

    async def route_to_team(
        self, *, tenant_id, conversation_id, team_id, reason, initiated_by, now
    ) -> bool:
        from iam_platform.domain.ai_resources.entities import ConversationState

        c = self._get(tenant_id, conversation_id)
        if c is None or c.state in (  # type: ignore[attr-defined]
            ConversationState.ASSIGNED,
            ConversationState.HUMAN_ACTIVE,
        ):
            return False
        c.route_to_team(  # type: ignore[attr-defined]
            team_id=team_id, reason=reason, initiated_by=initiated_by, now=now
        )
        return True

    async def claim(self, *, tenant_id, conversation_id, membership_id, now) -> bool:
        from iam_platform.domain.ai_resources.entities import ConversationState

        c = self._get(tenant_id, conversation_id)
        if c is None or c.state is not ConversationState.UNASSIGNED:  # type: ignore[attr-defined]
            return False
        c.claim(membership_id=membership_id, now=now)  # type: ignore[attr-defined]
        return True

    async def set_state(
        self, *, tenant_id, conversation_id, state, now, clear_assignment=False
    ) -> bool:
        c = self._get(tenant_id, conversation_id)
        if c is None:
            return False
        c.state = state  # type: ignore[attr-defined]
        if clear_assignment:
            c.assigned_membership_id = None  # type: ignore[attr-defined]
            c.assigned_team_id = None  # type: ignore[attr-defined]
        return True

    async def set_ai_fallback_disabled(
        self, *, tenant_id, conversation_id, disabled, now
    ) -> bool:
        c = self._get(tenant_id, conversation_id)
        if c is None:
            return False
        c.ai_fallback_disabled = disabled  # type: ignore[attr-defined]
        return True

    async def list_unassigned(self, *, tenant_id, team_ids=None) -> list[object]:
        from iam_platform.domain.ai_resources.entities import ConversationState

        return [
            c
            for c in self._conversations.by_id.values()  # type: ignore[attr-defined]
            if c.tenant_id == tenant_id
            and c.state is ConversationState.UNASSIGNED
            and (team_ids is None or c.assigned_team_id in team_ids)
        ]

class FakeAiResourceUnitOfWork:
    _REPO_ATTRS = (
        "tenant_memberships",
        "assistants",
        "assistant_members",
        "knowledge_bases",
        "documents",
        "data_sources",
        "chat_widgets",
        "conversations",
        "conversation_messages",
        "model_configurations",
        "provider_credentials",
        "audit",
        "security_events",
        "entitlements",
        "chatbot_settings",
        "teams",
    )

    def __init__(self) -> None:
        self.tenant_memberships = FakeTenantMembershipRepository()
        self.assistants = FakeAiAssistantRepository()
        self.assistant_members = FakeAssistantMemberRepository()
        self.knowledge_bases = FakeKnowledgeBaseRepository()
        self.documents = FakeDocumentRepository()
        self.data_sources = FakeDataSourceRepository()
        self.chat_widgets = FakeChatWidgetRepository()
        self.conversations = FakeConversationRepository()
        self.conversation_messages = FakeConversationMessageRepository()
        self.model_configurations = FakeModelConfigurationRepository()
        self.provider_credentials = FakeProviderCredentialRepository()
        self.entitlements = FakeTenantEntitlementRepository()
        self.chatbot_settings = FakeTenantChatbotSettingsRepository()
        self.teams = FakeTenantTeamRepository()
        self.handoff = FakeConversationHandoffRepository(self.conversations)
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
