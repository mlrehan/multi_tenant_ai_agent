"""``/v1/tenants/{tenant_id}/...`` -- assistants, knowledge bases, documents,
conversations, and provider credentials.

Every handler resolves the caller's effective tenant permissions through the
standard dependency chain and passes them into the use case, which applies
both the coarse permission check and the per-resource visibility policy.
The router itself makes no authorization decisions -- that keeps the policy
in one testable place instead of split between here and the use case.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.deps.permission_resolver import get_effective_tenant_permissions
from iam_platform.api.v1.assistants import schemas
from iam_platform.application.ai_resources.answer_question import (
    AnswerQuestion,
    AnswerQuestionQuery,
)
from iam_platform.application.ai_resources.exceptions import (
    DocumentTooLargeError,
    UnsupportedDocumentTypeError,
)
from iam_platform.application.ai_resources.manage_assistant import (
    ArchiveAssistant,
    ArchiveAssistantCommand,
    ChangeAssistantVisibility,
    ChangeAssistantVisibilityCommand,
    CreateAssistant,
    CreateAssistantCommand,
    GetAssistant,
    GetAssistantQuery,
    ListAssistants,
    ListAssistantsQuery,
    ListModelConfigurations,
    ListModelConfigurationsQuery,
    PublishAssistant,
    PublishAssistantCommand,
    UpdateAssistant,
    UpdateAssistantCommand,
)
from iam_platform.application.ai_resources.manage_assistant_members import (
    GrantAssistantAccess,
    GrantAssistantAccessCommand,
    RevokeAssistantAccess,
    RevokeAssistantAccessCommand,
)
from iam_platform.application.ai_resources.manage_chat_widget import (
    CreateChatWidget,
    CreateChatWidgetCommand,
    ListChatWidgets,
    ListChatWidgetsQuery,
    SetChatWidgetStatus,
    SetChatWidgetStatusCommand,
)
from iam_platform.application.ai_resources.manage_conversation import (
    GetConversation,
    GetConversationQuery,
    ListMyConversations,
    ListMyConversationsQuery,
    StartConversation,
    StartConversationCommand,
)
from iam_platform.application.ai_resources.manage_data_source import (
    CreateDataSource,
    CreateDataSourceCommand,
    ListDataSources,
    ListDataSourcesQuery,
    ResyncDataSource,
    ResyncDataSourceCommand,
)
from iam_platform.application.ai_resources.manage_document import (
    DeleteDocument,
    DocumentActionCommand,
    GetDocumentDetail,
    GetDocumentDetailQuery,
    RetryDocumentIngestion,
)
from iam_platform.application.ai_resources.manage_knowledge_base import (
    CreateKnowledgeBase,
    CreateKnowledgeBaseCommand,
    DocumentSummary,
    ListDocuments,
    ListDocumentsQuery,
    ListKnowledgeBases,
    ListKnowledgeBasesQuery,
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
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    ChatWidget,
    DataSource,
    KnowledgeBase,
    ModelConfiguration,
)

logger = logging.getLogger("iam_platform.api.v1.assistants")

router = APIRouter(prefix="/v1/tenants/{tenant_id}", tags=["ai-resources"])

#: Per-file upload cap. Generous enough for a large scanned PDF, small
#: enough that one upload cannot exhaust a worker's memory during parsing.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _assistant_response(assistant: AiAssistant) -> schemas.AssistantResponse:
    return schemas.AssistantResponse(
        id=assistant.id,
        name=assistant.name,
        description=assistant.description,
        visibility=assistant.visibility.value,
        department_id=assistant.department_id,
        team_id=assistant.team_id,
        owner_membership_id=assistant.owner_membership_id,
        model_configuration_id=assistant.model_configuration_id,
        system_prompt=assistant.system_prompt,
        status=assistant.status.value,
        created_at=assistant.created_at,
        updated_at=assistant.updated_at,
    )


def _model_configuration_response(mc: ModelConfiguration) -> schemas.ModelConfigurationResponse:
    return schemas.ModelConfigurationResponse(
        id=mc.id,
        model_name=mc.model_name,
    )


def _embed_snippet(widget: ChatWidget, base_url: str) -> str:
    """The exact line a tenant pastes into their site.

    Returned rather than assembled in the console: the browser there does not
    know this API's public origin (the console reaches it through a server-side
    proxy and ships no public backend URL), so anything it built would point at
    the wrong host.
    """
    src = f"{base_url.rstrip('/')}/v1/public/chat/widget.js"
    return f'<script src="{src}" data-public-key="{widget.public_key}" async></script>'


def _public_base_url(request: Request, container: AppContainer) -> str:
    configured = container.settings.public_api_base_url.strip()
    # The request's own base URL is correct in development and behind a proxy
    # that forwards a truthful Host; the setting exists for the ones that do not.
    return configured or str(request.base_url)


def _chat_widget_response(
    widget: ChatWidget, base_url: str
) -> schemas.ChatWidgetResponse:
    return schemas.ChatWidgetResponse(
        id=widget.id,
        knowledge_base_id=widget.knowledge_base_id,
        name=widget.name,
        public_key=widget.public_key,
        allowed_origins=widget.allowed_origins,
        status=widget.status.value,
        daily_question_limit=widget.daily_question_limit,
        created_at=widget.created_at,
        embed_snippet=_embed_snippet(widget, base_url),
    )


def _data_source_response(source: DataSource) -> schemas.DataSourceResponse:
    return schemas.DataSourceResponse(
        id=source.id,
        knowledge_base_id=source.knowledge_base_id,
        urls=source.urls,
        mode=source.mode.value,
        sync_status=source.sync_status.value,
        failure_reason=source.failure_reason,
        pages_discovered=source.pages_discovered,
        pages_indexed=source.pages_indexed,
        last_synced_at=source.last_synced_at,
        created_at=source.created_at,
    )


def _document_response(summary: DocumentSummary) -> schemas.DocumentResponse:
    document = summary.document
    return schemas.DocumentResponse(
        id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        status=document.status.value,
        failure_reason=document.failure_reason,
        chunk_count=summary.chunk_count,
        created_at=document.created_at,
    )


def _knowledge_base_response(kb: KnowledgeBase) -> schemas.KnowledgeBaseResponse:
    return schemas.KnowledgeBaseResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        visibility=kb.visibility.value,
        owner_membership_id=kb.owner_membership_id,
        created_at=kb.created_at,
    )


def _credential_response(
    summary: ProviderCredentialSummary,
) -> schemas.ProviderCredentialResponse:
    return schemas.ProviderCredentialResponse(
        id=summary.id,
        provider=summary.provider,
        key_hint=summary.key_hint,
        created_at=summary.created_at,
        rotated_at=summary.rotated_at,
        revoked_at=summary.revoked_at,
    )


# --- Assistants --------------------------------------------------------------


@router.post(
    "/assistants",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.CreateAssistantResponse,
)
async def create_assistant(
    tenant_id: str,
    body: schemas.CreateAssistantRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateAssistantResponse:
    use_case = CreateAssistant(container.ai_resource_uow_factory, container.clock)
    assistant_id = await use_case.execute(
        CreateAssistantCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
            name=body.name,
            description=body.description,
            model_configuration_id=str(body.model_configuration_id),
            visibility=body.visibility,
            department_id=str(body.department_id) if body.department_id else None,
            team_id=str(body.team_id) if body.team_id else None,
            system_prompt=body.system_prompt,
        )
    )
    return schemas.CreateAssistantResponse(id=assistant_id)


@router.get("/assistants", response_model=schemas.AssistantListResponse)
async def list_assistants(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.AssistantListResponse:
    use_case = ListAssistants(container.ai_resource_uow_factory)
    assistants = await use_case.execute(
        ListAssistantsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, permissions=permissions
        )
    )
    return schemas.AssistantListResponse(
        assistants=[_assistant_response(a) for a in assistants]
    )


@router.get("/assistants/{assistant_id}", response_model=schemas.AssistantResponse)
async def get_assistant(
    tenant_id: str,
    assistant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.AssistantResponse:
    use_case = GetAssistant(container.ai_resource_uow_factory)
    assistant = await use_case.execute(
        GetAssistantQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            permissions=permissions,
        )
    )
    return _assistant_response(assistant)


@router.post("/assistants/{assistant_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_assistant(
    tenant_id: str,
    assistant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = PublishAssistant(container.ai_resource_uow_factory, container.clock)
    await use_case.execute(
        PublishAssistantCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            permissions=permissions,
        )
    )


@router.patch("/assistants/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_assistant(
    tenant_id: str,
    assistant_id: str,
    body: schemas.UpdateAssistantRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = UpdateAssistant(container.ai_resource_uow_factory, container.clock)
    await use_case.execute(
        UpdateAssistantCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            permissions=permissions,
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            model_configuration_id=str(body.model_configuration_id),
        )
    )


@router.post("/assistants/{assistant_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_assistant(
    tenant_id: str,
    assistant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = ArchiveAssistant(container.ai_resource_uow_factory, container.clock)
    await use_case.execute(
        ArchiveAssistantCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            permissions=permissions,
        )
    )


@router.get("/model-configurations", response_model=schemas.ModelConfigurationListResponse)
async def list_model_configurations(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ModelConfigurationListResponse:
    use_case = ListModelConfigurations(container.ai_resource_uow_factory)
    configs = await use_case.execute(
        ListModelConfigurationsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, permissions=permissions
        )
    )
    return schemas.ModelConfigurationListResponse(
        model_configurations=[_model_configuration_response(c) for c in configs]
    )


@router.put("/assistants/{assistant_id}/visibility", status_code=status.HTTP_204_NO_CONTENT)
async def change_assistant_visibility(
    tenant_id: str,
    assistant_id: str,
    body: schemas.ChangeVisibilityRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = ChangeAssistantVisibility(container.ai_resource_uow_factory, container.clock)
    await use_case.execute(
        ChangeAssistantVisibilityCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            permissions=permissions,
            visibility=body.visibility,
            department_id=str(body.department_id) if body.department_id else None,
            team_id=str(body.team_id) if body.team_id else None,
        )
    )


@router.post(
    "/assistants/{assistant_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.GrantAssistantAccessResponse,
)
async def grant_assistant_access(
    tenant_id: str,
    assistant_id: str,
    body: schemas.GrantAssistantAccessRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.GrantAssistantAccessResponse:
    use_case = GrantAssistantAccess(container.ai_resource_uow_factory, container.clock)
    member_id = await use_case.execute(
        GrantAssistantAccessCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            target_membership_id=str(body.membership_id),
            access_level=body.access_level,
            permissions=permissions,
        )
    )
    return schemas.GrantAssistantAccessResponse(id=member_id)


@router.delete(
    "/assistants/{assistant_id}/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_assistant_access(
    tenant_id: str,
    assistant_id: str,
    membership_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RevokeAssistantAccess(container.ai_resource_uow_factory)
    await use_case.execute(
        RevokeAssistantAccessCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            target_membership_id=membership_id,
            permissions=permissions,
        )
    )


# --- Knowledge bases and documents -------------------------------------------


@router.post(
    "/knowledge-bases",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.CreateKnowledgeBaseResponse,
)
async def create_knowledge_base(
    tenant_id: str,
    body: schemas.CreateKnowledgeBaseRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateKnowledgeBaseResponse:
    use_case = CreateKnowledgeBase(
        container.ai_resource_uow_factory, container.vector_namespace_factory, container.clock
    )
    knowledge_base_id = await use_case.execute(
        CreateKnowledgeBaseCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
            name=body.name,
            description=body.description,
            visibility=body.visibility,
            department_id=str(body.department_id) if body.department_id else None,
            team_id=str(body.team_id) if body.team_id else None,
        )
    )
    return schemas.CreateKnowledgeBaseResponse(id=knowledge_base_id)


@router.get("/knowledge-bases", response_model=schemas.KnowledgeBaseListResponse)
async def list_knowledge_bases(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.KnowledgeBaseListResponse:
    use_case = ListKnowledgeBases(container.ai_resource_uow_factory)
    knowledge_bases = await use_case.execute(
        ListKnowledgeBasesQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, permissions=permissions
        )
    )
    return schemas.KnowledgeBaseListResponse(
        knowledge_bases=[_knowledge_base_response(kb) for kb in knowledge_bases]
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.UploadDocumentResponse,
)
async def upload_document(
    tenant_id: str,
    knowledge_base_id: str,
    file: UploadFile = File(...),
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.UploadDocumentResponse:
    """Real multipart upload -- receives the bytes, not just metadata.

    Size and type are enforced here, at the boundary, before anything reaches
    storage or the queue. `UploadFile` buffers to a spool file rather than
    holding the whole body in memory, so the read below is bounded by the
    check that precedes it.
    """
    content = await _read_within_limit(file)

    declared_type = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"
    if not container.document_parser.supports(
        content_type=declared_type, filename=filename
    ):
        # Refused now rather than accepted and failed asynchronously: the
        # person is still at the keyboard, and a 415 they can read beats a
        # document that turns red in the list five minutes later.
        raise UnsupportedDocumentTypeError(
            f"{filename}: {declared_type} is not a supported document type"
        )

    use_case = UploadDocument(
        container.ai_resource_uow_factory,
        container.storage_path_factory,
        container.document_ingestion_queue,
        container.clock,
        container.object_storage_client,
    )
    document_id = await use_case.execute(
        UploadDocumentCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            permissions=permissions,
            filename=filename,
            content_type=declared_type,
            size_bytes=len(content),
            # Server-computed, never client-supplied: a checksum the client
            # chose would attest to nothing.
            checksum=hashlib.sha256(content).hexdigest(),
            content=content,
        )
    )
    return schemas.UploadDocumentResponse(id=document_id)


async def _read_within_limit(file: UploadFile) -> bytes:
    """Reads the upload, refusing anything over the size cap.

    Read in chunks with a running total rather than `await file.read()` then
    checking `len()`: the latter would have already materialised an arbitrarily
    large body before deciding to reject it, which is the denial-of-service the
    limit exists to prevent.
    """
    chunks: list[bytes] = []
    total = 0
    while segment := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(segment)
        if total > MAX_UPLOAD_BYTES:
            raise DocumentTooLargeError(
                f"upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
            )
        chunks.append(segment)
    return b"".join(chunks)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=schemas.DocumentListResponse,
)
async def list_documents(
    tenant_id: str,
    knowledge_base_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.DocumentListResponse:
    use_case = ListDocuments(container.ai_resource_uow_factory)
    documents = await use_case.execute(
        ListDocumentsQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            permissions=permissions,
        )
    )
    return schemas.DocumentListResponse(
        documents=[_document_response(d) for d in documents]
    )


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
    response_model=schemas.DocumentDetailResponse,
)
async def get_document_detail(
    tenant_id: str,
    knowledge_base_id: str,
    document_id: str,
    limit: int = 50,
    offset: int = 0,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.DocumentDetailResponse:
    """The document plus the text that was actually indexed from it.

    Read access to the knowledge base is enough: the same passages are already
    reachable by asking the knowledge base a question, so requiring modify
    rights here would withhold the diagnosis from the people who need it while
    protecting nothing.
    """
    use_case = GetDocumentDetail(container.ai_resource_uow_factory)
    detail = await use_case.execute(
        GetDocumentDetailQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            permissions=permissions,
            limit=limit,
            offset=offset,
        )
    )
    return schemas.DocumentDetailResponse(
        document=_document_response(DocumentSummary(detail.document, detail.chunk_count)),
        chunks=[
            schemas.DocumentChunkResponse(
                id=c.chunk_id,
                chunk_index=c.chunk_index,
                text=c.text,
                token_count=c.token_count,
                source_location=c.source_location,
            )
            for c in detail.chunks
        ],
        chunk_count=detail.chunk_count,
        source_url=detail.document.source_url,
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(
    tenant_id: str,
    knowledge_base_id: str,
    document_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Re-runs ingestion for a document whose bytes are already stored.

    202, not 200: the work happens in a worker, and the response means "queued"
    rather than "indexed". The document's status is the record of what actually
    happened.
    """
    use_case = RetryDocumentIngestion(
        container.ai_resource_uow_factory,
        container.document_ingestion_queue,
        container.clock,
    )
    await use_case.execute(
        DocumentActionCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    tenant_id: str,
    knowledge_base_id: str,
    document_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Removes a document, its chunks, its vectors and its stored bytes."""
    use_case = DeleteDocument(
        container.ai_resource_uow_factory,
        container.object_storage_client,
        container.vector_search_client,
        container.clock,
    )
    await use_case.execute(
        DocumentActionCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/data-sources",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.CreateDataSourceResponse,
)
async def create_data_source(
    tenant_id: str,
    knowledge_base_id: str,
    body: schemas.CreateDataSourceRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateDataSourceResponse:
    """Registers a crawl and enqueues it.

    The URLs are checked against the SSRF guard inside the use case, before
    anything is persisted -- so a refused target is a 400 the tenant can act
    on, not a job that fails silently minutes later. The crawler re-checks
    every URL it is about to fetch, including links it discovers itself, which
    is the half no boundary check can cover.
    """
    use_case = CreateDataSource(
        container.ai_resource_uow_factory,
        container.crawl_job_queue,
        container.url_validator,
        container.clock,
    )
    source_id = await use_case.execute(
        CreateDataSourceCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            permissions=permissions,
            # `HttpUrl` round-trips to a normalized string; the domain and the
            # crawler both deal in plain strings.
            urls=[str(u) for u in body.urls],
            mode=body.mode,
        )
    )
    return schemas.CreateDataSourceResponse(id=source_id)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/data-sources",
    response_model=schemas.DataSourceListResponse,
)
async def list_data_sources(
    tenant_id: str,
    knowledge_base_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.DataSourceListResponse:
    use_case = ListDataSources(container.ai_resource_uow_factory)
    sources = await use_case.execute(
        ListDataSourcesQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            permissions=permissions,
        )
    )
    return schemas.DataSourceListResponse(
        data_sources=[_data_source_response(s) for s in sources]
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/data-sources/{data_source_id}/resync",
    status_code=status.HTTP_202_ACCEPTED,
)
async def resync_data_source(
    tenant_id: str,
    knowledge_base_id: str,
    data_source_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Re-crawls an existing source.

    Enqueues the same job creation does. That job updates a page's existing
    document rather than inserting a second one, so a re-sync refreshes
    changed pages and picks up new ones without duplicating anything.
    """
    use_case = ResyncDataSource(
        container.ai_resource_uow_factory,
        container.crawl_job_queue,
        container.url_validator,
        container.clock,
    )
    await use_case.execute(
        ResyncDataSourceCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            data_source_id=data_source_id,
            permissions=permissions,
        )
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/chat-widgets",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.ChatWidgetResponse,
)
async def create_chat_widget(
    tenant_id: str,
    body: schemas.CreateChatWidgetRequest,
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ChatWidgetResponse:
    """Publishes a knowledge base to the open internet, via an embeddable widget.

    The public key is generated server-side and returned once here and on every
    subsequent list -- it is not a secret (it ships in a script tag) and the
    tenant needs to copy it out.
    """
    use_case = CreateChatWidget(container.ai_resource_uow_factory, container.clock)
    widget = await use_case.execute(
        CreateChatWidgetCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=str(body.knowledge_base_id),
            permissions=permissions,
            name=body.name,
            allowed_origins=[str(o) for o in body.allowed_origins],
            daily_question_limit=body.daily_question_limit,
        )
    )
    return _chat_widget_response(widget, _public_base_url(request, container))


@router.get("/chat-widgets", response_model=schemas.ChatWidgetListResponse)
async def list_chat_widgets(
    tenant_id: str,
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ChatWidgetListResponse:
    use_case = ListChatWidgets(container.ai_resource_uow_factory)
    widgets = await use_case.execute(
        ListChatWidgetsQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
        )
    )
    base_url = _public_base_url(request, container)
    return schemas.ChatWidgetListResponse(
        chat_widgets=[_chat_widget_response(w, base_url) for w in widgets]
    )


@router.post(
    "/chat-widgets/{widget_id}/status", response_model=schemas.ChatWidgetResponse
)
async def set_chat_widget_status(
    tenant_id: str,
    widget_id: str,
    body: schemas.SetChatWidgetStatusRequest,
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ChatWidgetResponse:
    """Switches a public widget off, or back on.

    One route with a boolean rather than `/disable` and `/enable`: the two
    differ only in the value written, and a single path means an operator
    hunting for the off switch finds it whichever state the widget is in.
    """
    use_case = SetChatWidgetStatus(container.ai_resource_uow_factory)
    widget = await use_case.execute(
        SetChatWidgetStatusCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            widget_id=widget_id,
            permissions=permissions,
            enabled=body.enabled,
        )
    )
    return _chat_widget_response(widget, _public_base_url(request, container))


@router.post("/knowledge-bases/{knowledge_base_id}/answer")
async def answer_question(
    tenant_id: str,
    knowledge_base_id: str,
    body: schemas.AnswerQuestionRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    """A grounded, cited answer, streamed as Server-Sent Events.

    SSE rather than WebSockets: this is one-directional (a question in, tokens
    out), and SSE survives corporate proxies and CDNs that drop WebSocket
    upgrades. Revisit only if a requirement appears that genuinely needs
    bidirectional messages.

    **Citations are sent first, before any token.** They are the passages the
    model was *given*, which is known before generation starts -- so a client
    can render sources immediately, and a reader can see what the answer was
    allowed to draw on even if generation fails midway.
    """
    use_case = AnswerQuestion(
        container.ai_resource_uow_factory,
        container.vector_search_client,
        container.reranker,
        container.chat_model,
    )
    result = await use_case.execute(
        AnswerQuestionQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            permissions=permissions,
            question=body.question,
        )
    )

    async def events() -> AsyncIterator[str]:
        yield _sse(
            "sources",
            {
                "citations": [
                    {
                        "label": c.label,
                        "document_id": str(c.document_id),
                        "source_location": c.source_location,
                        "relevance": c.relevance,
                    }
                    for c in result.citations
                ]
            },
        )
        try:
            async for token in result.tokens:
                yield _sse("token", {"text": token})
        except Exception:
            # The response has already begun, so the status code is long since
            # sent -- an exception here cannot become a 500. Emitting an
            # explicit error event is the only way the client learns the answer
            # is incomplete rather than merely short.
            logger.exception("answer stream failed for knowledge base %s", knowledge_base_id)
            yield _sse("error", {"detail": "the answer could not be completed"})
            return
        yield _sse("done", {"cited": sorted(result.cited_labels)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # Without this an nginx or CDN buffer holds tokens until the
            # response completes, which converts streaming back into waiting.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-store",
        },
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    """One Server-Sent Event.

    The blank line at the end is the frame delimiter -- without it the client
    buffers indefinitely waiting for a message that never completes.
    """
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post(
    "/knowledge-bases/{knowledge_base_id}/query",
    response_model=schemas.QueryKnowledgeBaseResponse,
)
async def query_knowledge_base(
    tenant_id: str,
    knowledge_base_id: str,
    body: schemas.QueryKnowledgeBaseRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.QueryKnowledgeBaseResponse:
    use_case = QueryKnowledgeBase(
        container.ai_resource_uow_factory, container.vector_search_client
    )
    hits = await use_case.execute(
        QueryKnowledgeBaseQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            permissions=permissions,
            query_text=body.query_text,
            top_k=body.top_k,
        )
    )
    return schemas.QueryKnowledgeBaseResponse(
        hits=[
            schemas.SearchHitResponse(
                document_id=h.document_id, filename=h.filename, score=h.score
            )
            for h in hits
        ]
    )


# --- Conversations -----------------------------------------------------------


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.StartConversationResponse,
)
async def start_conversation(
    tenant_id: str,
    body: schemas.StartConversationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.StartConversationResponse:
    use_case = StartConversation(container.ai_resource_uow_factory, container.clock)
    conversation_id = await use_case.execute(
        StartConversationCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            assistant_id=str(body.assistant_id),
            permissions=permissions,
            title=body.title,
        )
    )
    return schemas.StartConversationResponse(id=conversation_id)


@router.get("/conversations", response_model=schemas.ConversationListResponse)
async def list_my_conversations(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ConversationListResponse:
    use_case = ListMyConversations(container.ai_resource_uow_factory)
    conversations = await use_case.execute(
        ListMyConversationsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, permissions=permissions
        )
    )
    return schemas.ConversationListResponse(
        conversations=[
            schemas.ConversationResponse(
                id=c.id,
                assistant_id=c.assistant_id,
                membership_id=c.membership_id,
                title=c.title,
                status=c.status.value,
                created_at=c.created_at,
                last_message_at=c.last_message_at,
            )
            for c in conversations
        ]
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=schemas.ConversationResponse | schemas.ConversationSummaryResponse,
)
async def get_conversation(
    tenant_id: str,
    conversation_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ConversationResponse | schemas.ConversationSummaryResponse:
    use_case = GetConversation(container.ai_resource_uow_factory)
    view = await use_case.execute(
        GetConversationQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            permissions=permissions,
        )
    )
    c = view.conversation
    if not view.is_owner:
        # Metadata-only for an auditor reaching someone else's conversation
        # (docs/16). The title is withheld too -- see the schema's docstring.
        return schemas.ConversationSummaryResponse(
            id=c.id,
            assistant_id=c.assistant_id,
            membership_id=c.membership_id,
            status=c.status.value,
            created_at=c.created_at,
            last_message_at=c.last_message_at,
        )
    return schemas.ConversationResponse(
        id=c.id,
        assistant_id=c.assistant_id,
        membership_id=c.membership_id,
        title=c.title,
        status=c.status.value,
        created_at=c.created_at,
        last_message_at=c.last_message_at,
    )


# --- Provider credentials ----------------------------------------------------


@router.post(
    "/provider-credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.ProviderCredentialResponse,
)
async def store_provider_credential(
    tenant_id: str,
    body: schemas.StoreProviderCredentialRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ProviderCredentialResponse:
    use_case = StoreProviderCredential(
        container.ai_resource_uow_factory, container.credential_encryptor, container.clock
    )
    summary = await use_case.execute(
        StoreProviderCredentialCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            permissions=permissions,
            provider=body.provider,
            secret=body.secret,
        )
    )
    return _credential_response(summary)


@router.get("/provider-credentials", response_model=schemas.ProviderCredentialListResponse)
async def list_provider_credentials(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ProviderCredentialListResponse:
    use_case = ListProviderCredentials(container.ai_resource_uow_factory)
    summaries = await use_case.execute(
        ListProviderCredentialsQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, permissions=permissions
        )
    )
    return schemas.ProviderCredentialListResponse(
        credentials=[_credential_response(s) for s in summaries]
    )


@router.post(
    "/provider-credentials/{credential_id}/rotate",
    response_model=schemas.ProviderCredentialResponse,
)
async def rotate_provider_credential(
    tenant_id: str,
    credential_id: str,
    body: schemas.RotateProviderCredentialRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> schemas.ProviderCredentialResponse:
    use_case = RotateProviderCredential(
        container.ai_resource_uow_factory, container.credential_encryptor, container.clock
    )
    summary = await use_case.execute(
        RotateProviderCredentialCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            credential_id=credential_id,
            permissions=permissions,
            new_secret=body.new_secret,
        )
    )
    return _credential_response(summary)


@router.delete(
    "/provider-credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_provider_credential(
    tenant_id: str,
    credential_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RevokeProviderCredential(container.ai_resource_uow_factory, container.clock)
    await use_case.execute(
        RevokeProviderCredentialCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            credential_id=credential_id,
            permissions=permissions,
        )
    )
