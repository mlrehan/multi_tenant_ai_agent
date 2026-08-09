"""SQLAlchemy implementations of the AI-resource repository ports.

Every query here relies on RLS for tenant scoping rather than adding a
redundant ``WHERE tenant_id = ...`` -- see docs/18-schema-rls-and-migrations.md.
The exception is ``list_available_to_tenant`` on model configurations, where
the nullable-tenant "platform defaults plus my own" split is a *business*
filter, not an isolation one, and has to be expressed in SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantAccessLevel,
    AssistantMember,
    AssistantStatus,
    ChatWidget,
    Conversation,
    ConversationStatus,
    CrawlMode,
    CredentialOwnerType,
    DataSource,
    DataSourceKind,
    Document,
    DocumentStatus,
    KnowledgeBase,
    ModelConfiguration,
    ProviderCredential,
    ResourceVisibility,
    SyncStatus,
    WidgetStatus,
)
from iam_platform.infrastructure.db.models.ai_resources import (
    AiAssistantModel,
    AssistantMemberModel,
    ChatWidgetModel,
    ConversationModel,
    DataSourceModel,
    DocumentModel,
    KnowledgeBaseModel,
    ModelConfigurationModel,
    ProviderCredentialModel,
)


def _assistant_to_domain(m: AiAssistantModel) -> AiAssistant:
    return AiAssistant(
        id=m.id,
        tenant_id=m.tenant_id,
        name=m.name,
        description=m.description,
        visibility=ResourceVisibility(m.visibility),
        department_id=m.department_id,
        team_id=m.team_id,
        owner_membership_id=m.owner_membership_id,
        model_configuration_id=m.model_configuration_id,
        status=AssistantStatus(m.status),
        system_prompt=m.system_prompt,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlAiAssistantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, assistant_id: UUID) -> AiAssistant | None:
        model = await self._session.get(AiAssistantModel, assistant_id)
        return _assistant_to_domain(model) if model else None

    async def list_by_tenant(self, tenant_id: UUID) -> list[AiAssistant]:
        stmt = (
            select(AiAssistantModel)
            .where(AiAssistantModel.status != AssistantStatus.ARCHIVED.value)
            .order_by(AiAssistantModel.created_at)
        )
        return [_assistant_to_domain(m) for m in (await self._session.execute(stmt)).scalars()]

    async def add(self, assistant: AiAssistant) -> None:
        self._session.add(
            AiAssistantModel(
                id=assistant.id,
                tenant_id=assistant.tenant_id,
                name=assistant.name,
                description=assistant.description,
                visibility=assistant.visibility.value,
                department_id=assistant.department_id,
                team_id=assistant.team_id,
                owner_membership_id=assistant.owner_membership_id,
                model_configuration_id=assistant.model_configuration_id,
                status=assistant.status.value,
                system_prompt=assistant.system_prompt,
            )
        )
        await self._session.flush()

    async def save(self, assistant: AiAssistant) -> None:
        await self._session.execute(
            update(AiAssistantModel)
            .where(AiAssistantModel.id == assistant.id)
            .values(
                name=assistant.name,
                description=assistant.description,
                visibility=assistant.visibility.value,
                department_id=assistant.department_id,
                team_id=assistant.team_id,
                status=assistant.status.value,
                system_prompt=assistant.system_prompt,
            )
        )


def _assistant_member_to_domain(m: AssistantMemberModel) -> AssistantMember:
    return AssistantMember(
        id=m.id,
        tenant_id=m.tenant_id,
        assistant_id=m.assistant_id,
        membership_id=m.membership_id,
        access_level=AssistantAccessLevel(m.access_level),
        added_at=m.added_at,
    )


class SqlAssistantMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, assistant_id: UUID, membership_id: UUID) -> AssistantMember | None:
        stmt = select(AssistantMemberModel).where(
            AssistantMemberModel.assistant_id == assistant_id,
            AssistantMemberModel.membership_id == membership_id,
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _assistant_member_to_domain(model) if model else None

    async def list_by_assistant(self, assistant_id: UUID) -> list[AssistantMember]:
        stmt = select(AssistantMemberModel).where(
            AssistantMemberModel.assistant_id == assistant_id
        )
        return [
            _assistant_member_to_domain(m) for m in (await self._session.execute(stmt)).scalars()
        ]

    async def list_for_membership(self, membership_id: UUID) -> list[AssistantMember]:
        stmt = select(AssistantMemberModel).where(
            AssistantMemberModel.membership_id == membership_id
        )
        return [
            _assistant_member_to_domain(m) for m in (await self._session.execute(stmt)).scalars()
        ]

    async def add(self, member: AssistantMember) -> None:
        self._session.add(
            AssistantMemberModel(
                id=member.id,
                tenant_id=member.tenant_id,
                assistant_id=member.assistant_id,
                membership_id=member.membership_id,
                access_level=member.access_level.value,
                added_at=member.added_at,
            )
        )
        await self._session.flush()

    async def remove(self, *, assistant_id: UUID, membership_id: UUID) -> None:
        await self._session.execute(
            delete(AssistantMemberModel).where(
                AssistantMemberModel.assistant_id == assistant_id,
                AssistantMemberModel.membership_id == membership_id,
            )
        )


def _knowledge_base_to_domain(m: KnowledgeBaseModel) -> KnowledgeBase:
    return KnowledgeBase(
        id=m.id,
        tenant_id=m.tenant_id,
        name=m.name,
        description=m.description,
        owner_membership_id=m.owner_membership_id,
        visibility=ResourceVisibility(m.visibility),
        department_id=m.department_id,
        team_id=m.team_id,
        vector_namespace=m.vector_namespace,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlKnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        model = await self._session.get(KnowledgeBaseModel, knowledge_base_id)
        return _knowledge_base_to_domain(model) if model else None

    async def list_by_tenant(self, tenant_id: UUID) -> list[KnowledgeBase]:
        stmt = select(KnowledgeBaseModel).order_by(KnowledgeBaseModel.created_at)
        return [_knowledge_base_to_domain(m) for m in (await self._session.execute(stmt)).scalars()]

    async def add(self, knowledge_base: KnowledgeBase) -> None:
        self._session.add(
            KnowledgeBaseModel(
                id=knowledge_base.id,
                tenant_id=knowledge_base.tenant_id,
                name=knowledge_base.name,
                description=knowledge_base.description,
                owner_membership_id=knowledge_base.owner_membership_id,
                visibility=knowledge_base.visibility.value,
                department_id=knowledge_base.department_id,
                team_id=knowledge_base.team_id,
                vector_namespace=knowledge_base.vector_namespace,
            )
        )
        await self._session.flush()

    async def save(self, knowledge_base: KnowledgeBase) -> None:
        await self._session.execute(
            update(KnowledgeBaseModel)
            .where(KnowledgeBaseModel.id == knowledge_base.id)
            .values(
                name=knowledge_base.name,
                description=knowledge_base.description,
                visibility=knowledge_base.visibility.value,
                department_id=knowledge_base.department_id,
                team_id=knowledge_base.team_id,
            )
        )


def _document_to_domain(m: DocumentModel) -> Document:
    return Document(
        id=m.id,
        tenant_id=m.tenant_id,
        knowledge_base_id=m.knowledge_base_id,
        uploaded_by_membership_id=m.uploaded_by_membership_id,
        filename=m.filename,
        content_type=m.content_type,
        storage_path=m.storage_path,
        size_bytes=m.size_bytes,
        status=DocumentStatus(m.status),
        checksum=m.checksum,
        created_at=m.created_at,
        deleted_at=m.deleted_at,
        failure_reason=m.failure_reason,
    )


class SqlDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, document_id: UUID) -> Document | None:
        model = await self._session.get(DocumentModel, document_id)
        return _document_to_domain(model) if model else None

    async def list_by_knowledge_base(self, knowledge_base_id: UUID) -> list[Document]:
        stmt = (
            select(DocumentModel)
            .where(
                DocumentModel.knowledge_base_id == knowledge_base_id,
                DocumentModel.deleted_at.is_(None),
            )
            .order_by(DocumentModel.created_at)
        )
        return [_document_to_domain(m) for m in (await self._session.execute(stmt)).scalars()]

    async def add(self, document: Document) -> None:
        self._session.add(
            DocumentModel(
                id=document.id,
                tenant_id=document.tenant_id,
                knowledge_base_id=document.knowledge_base_id,
                uploaded_by_membership_id=document.uploaded_by_membership_id,
                filename=document.filename,
                content_type=document.content_type,
                storage_path=document.storage_path,
                size_bytes=document.size_bytes,
                status=document.status.value,
                checksum=document.checksum,
                created_at=document.created_at,
                deleted_at=document.deleted_at,
            )
        )
        await self._session.flush()

    async def save(self, document: Document) -> None:
        await self._session.execute(
            update(DocumentModel)
            .where(DocumentModel.id == document.id)
            .values(status=document.status.value, deleted_at=document.deleted_at)
        )


def _conversation_to_domain(m: ConversationModel) -> Conversation:
    return Conversation(
        id=m.id,
        tenant_id=m.tenant_id,
        assistant_id=m.assistant_id,
        membership_id=m.membership_id,
        title=m.title,
        status=ConversationStatus(m.status),
        created_at=m.created_at,
        updated_at=m.updated_at,
        last_message_at=m.last_message_at,
    )


class SqlConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        model = await self._session.get(ConversationModel, conversation_id)
        return _conversation_to_domain(model) if model else None

    async def list_by_membership(self, membership_id: UUID) -> list[Conversation]:
        stmt = (
            select(ConversationModel)
            .where(ConversationModel.membership_id == membership_id)
            .order_by(ConversationModel.last_message_at.desc().nullslast())
        )
        return [_conversation_to_domain(m) for m in (await self._session.execute(stmt)).scalars()]

    async def add(self, conversation: Conversation) -> None:
        self._session.add(
            ConversationModel(
                id=conversation.id,
                tenant_id=conversation.tenant_id,
                assistant_id=conversation.assistant_id,
                membership_id=conversation.membership_id,
                title=conversation.title,
                status=conversation.status.value,
                last_message_at=conversation.last_message_at,
            )
        )
        await self._session.flush()

    async def save(self, conversation: Conversation) -> None:
        await self._session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation.id)
            .values(
                title=conversation.title,
                status=conversation.status.value,
                last_message_at=conversation.last_message_at,
            )
        )


def _model_configuration_to_domain(m: ModelConfigurationModel) -> ModelConfiguration:
    return ModelConfiguration(
        id=m.id,
        tenant_id=m.tenant_id,
        provider_credential_id=m.provider_credential_id,
        model_name=m.model_name,
        parameters=dict(m.parameters),
        token_budget_per_month=m.token_budget_per_month,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlModelConfigurationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None:
        model = await self._session.get(ModelConfigurationModel, model_configuration_id)
        return _model_configuration_to_domain(model) if model else None

    async def list_available_to_tenant(self, tenant_id: UUID) -> list[ModelConfiguration]:
        stmt = select(ModelConfigurationModel).where(
            (ModelConfigurationModel.tenant_id.is_(None))
            | (ModelConfigurationModel.tenant_id == tenant_id)
        )
        return [
            _model_configuration_to_domain(m) for m in (await self._session.execute(stmt)).scalars()
        ]

    async def add(self, model_configuration: ModelConfiguration) -> None:
        self._session.add(
            ModelConfigurationModel(
                id=model_configuration.id,
                tenant_id=model_configuration.tenant_id,
                provider_credential_id=model_configuration.provider_credential_id,
                model_name=model_configuration.model_name,
                parameters=model_configuration.parameters,
                token_budget_per_month=model_configuration.token_budget_per_month,
            )
        )
        await self._session.flush()


def _provider_credential_to_domain(m: ProviderCredentialModel) -> ProviderCredential:
    return ProviderCredential(
        id=m.id,
        owner_type=CredentialOwnerType(m.owner_type),
        tenant_id=m.tenant_id,
        provider=m.provider,
        credential_ciphertext=m.credential_ciphertext,
        key_hint=m.key_hint,
        created_by_user_id=m.created_by_user_id,
        created_at=m.created_at,
        rotated_at=m.rotated_at,
        revoked_at=m.revoked_at,
    )


class SqlProviderCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, credential_id: UUID) -> ProviderCredential | None:
        model = await self._session.get(ProviderCredentialModel, credential_id)
        return _provider_credential_to_domain(model) if model else None

    async def list_by_tenant(self, tenant_id: UUID) -> list[ProviderCredential]:
        stmt = select(ProviderCredentialModel).order_by(ProviderCredentialModel.created_at)
        return [
            _provider_credential_to_domain(m) for m in (await self._session.execute(stmt)).scalars()
        ]

    async def add(self, credential: ProviderCredential) -> None:
        self._session.add(
            ProviderCredentialModel(
                id=credential.id,
                owner_type=credential.owner_type.value,
                tenant_id=credential.tenant_id,
                provider=credential.provider,
                credential_ciphertext=credential.credential_ciphertext,
                key_hint=credential.key_hint,
                created_by_user_id=credential.created_by_user_id,
                created_at=credential.created_at,
                rotated_at=credential.rotated_at,
                revoked_at=credential.revoked_at,
            )
        )
        await self._session.flush()

    async def save(self, credential: ProviderCredential) -> None:
        await self._session.execute(
            update(ProviderCredentialModel)
            .where(ProviderCredentialModel.id == credential.id)
            .values(
                credential_ciphertext=credential.credential_ciphertext,
                key_hint=credential.key_hint,
                rotated_at=credential.rotated_at,
                revoked_at=credential.revoked_at,
            )
        )


def _data_source_to_domain(model: DataSourceModel) -> DataSource:
    config = model.config or {}
    return DataSource(
        id=model.id,
        tenant_id=model.tenant_id,
        knowledge_base_id=model.knowledge_base_id,
        kind=DataSourceKind(model.kind),
        # `config` is JSONB, so these come back as whatever was stored. Coerced
        # defensively rather than trusted: a row written by a migration or a
        # fixture is not guaranteed to have been through the domain entity.
        urls=list(config.get("urls") or []),
        mode=CrawlMode(config.get("mode") or CrawlMode.URL_LIST.value),
        created_by_membership_id=model.created_by_membership_id,
        sync_status=SyncStatus(model.sync_status),
        failure_reason=model.failure_reason,
        pages_discovered=model.pages_discovered,
        pages_indexed=model.pages_indexed,
        last_synced_at=model.last_synced_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlDataSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, source: DataSource) -> None:
        self._session.add(
            DataSourceModel(
                id=source.id,
                tenant_id=source.tenant_id,
                knowledge_base_id=source.knowledge_base_id,
                kind=source.kind.value,
                # URLs and mode live in `config` because that is the column
                # docs/16 designates for non-secret source configuration. They
                # are read back out on the way to the domain entity, so nothing
                # outside this mapper deals in raw JSON.
                config={"urls": list(source.urls), "mode": source.mode.value},
                sync_status=source.sync_status.value,
                failure_reason=source.failure_reason,
                pages_discovered=source.pages_discovered,
                pages_indexed=source.pages_indexed,
                last_synced_at=source.last_synced_at,
                created_by_membership_id=source.created_by_membership_id,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, source_id: UUID) -> DataSource | None:
        model = await self._session.get(DataSourceModel, source_id)
        # The tenant check is belt-and-braces: RLS already scopes the read, so
        # a row from another tenant is invisible rather than filtered here.
        if model is None or model.tenant_id != tenant_id:
            return None
        return _data_source_to_domain(model)

    async def list_for_knowledge_base(
        self, *, tenant_id: UUID, knowledge_base_id: UUID
    ) -> list[DataSource]:
        stmt = (
            select(DataSourceModel)
            .where(
                DataSourceModel.tenant_id == tenant_id,
                DataSourceModel.knowledge_base_id == knowledge_base_id,
            )
            .order_by(DataSourceModel.created_at)
        )
        return [
            _data_source_to_domain(m)
            for m in (await self._session.execute(stmt)).scalars()
        ]


def _widget_to_domain(model: ChatWidgetModel) -> ChatWidget:
    return ChatWidget(
        id=model.id,
        tenant_id=model.tenant_id,
        knowledge_base_id=model.knowledge_base_id,
        name=model.name,
        public_key=model.public_key,
        allowed_origins=list(model.allowed_origins or []),
        status=WidgetStatus(model.status),
        daily_question_limit=model.daily_question_limit,
        created_by_membership_id=model.created_by_membership_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlChatWidgetRepository:
    """Tenant-scoped widget management, under RLS like every other repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, widget: ChatWidget) -> None:
        self._session.add(
            ChatWidgetModel(
                id=widget.id,
                tenant_id=widget.tenant_id,
                knowledge_base_id=widget.knowledge_base_id,
                name=widget.name,
                public_key=widget.public_key,
                allowed_origins=list(widget.allowed_origins),
                status=widget.status.value,
                daily_question_limit=widget.daily_question_limit,
                created_by_membership_id=widget.created_by_membership_id,
            )
        )
        await self._session.flush()

    async def list_for_tenant(self, tenant_id: UUID) -> list[ChatWidget]:
        stmt = (
            select(ChatWidgetModel)
            .where(ChatWidgetModel.tenant_id == tenant_id)
            .order_by(ChatWidgetModel.created_at)
        )
        return [
            _widget_to_domain(m) for m in (await self._session.execute(stmt)).scalars()
        ]

    async def get_for_tenant(self, tenant_id: UUID, widget_id: UUID) -> ChatWidget | None:
        stmt = select(ChatWidgetModel).where(
            ChatWidgetModel.tenant_id == tenant_id,
            ChatWidgetModel.id == widget_id,
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _widget_to_domain(model) if model is not None else None

    async def update(self, widget: ChatWidget) -> None:
        stmt = (
            update(ChatWidgetModel)
            .where(
                ChatWidgetModel.tenant_id == widget.tenant_id,
                ChatWidgetModel.id == widget.id,
            )
            .values(
                name=widget.name,
                allowed_origins=list(widget.allowed_origins),
                status=widget.status.value,
                daily_question_limit=widget.daily_question_limit,
            )
        )
        await self._session.execute(stmt)


class SqlPublicWidgetLookup:
    """The one read in this platform that crosses the tenant boundary.

    Runs on the **platform** (BYPASSRLS) connection, because a visitor supplies
    only a public key and the tenant is precisely what is being discovered --
    there is no RLS context to set yet. Kept deliberately narrow: two methods,
    each returning at most one row by a unique key, and the tenant that row
    carries then scopes everything downstream. No list, no filter, no search.
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None:
        return await self._one(ChatWidgetModel.public_key == public_key)

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return await self._one(ChatWidgetModel.id == widget_id)

    async def _one(self, condition: Any) -> ChatWidget | None:
        async with self._session_factory() as session:
            model = (
                await session.execute(select(ChatWidgetModel).where(condition))
            ).scalar_one_or_none()
            return _widget_to_domain(model) if model else None
