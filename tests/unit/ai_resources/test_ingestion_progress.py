"""Live ingestion progress: reported by the worker, read by the documents list.

The console used to show a file as "Processing" from upload to finish with
nothing in between. These prove the worker reports each stage in order with a
rising percentage, that the embedding stage really advances (it is requested
in slices when someone is listening), that reporting can never break an
ingestion, and that the list only ever reads progress for the caller's own
tenant and documents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.manage_knowledge_base import (
    ListDocuments,
    ListDocumentsQuery,
)
from iam_platform.application.ai_resources.ports import (
    IngestionProgress,
    IngestionStage,
    ParsedBlock,
    TextChunk,
)
from iam_platform.domain.ai_resources.entities import Document, DocumentStatus
from iam_platform.infrastructure.cache.ingestion_progress import RedisIngestionProgressStore
from iam_platform.workers.jobs.indexing import EMBED_SLICE, IndexingTarget, index_blocks
from iam_platform.workers.jobs.process_document_upload import process_document_upload
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_ingestion_job import (
    ACTOR_ID,
    DOCUMENT_ID,
    TENANT_ID,
    _dependencies,
    _factory,
    _FakeEmbeddings,
    _FakeSession,
    _FakeVectorSearch,
)
from tests.unit.ai_resources.test_knowledge_base_and_secrets import (
    QUERY,
    _seed_knowledge_base,
    _seed_member,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, tzinfo=UTC)


class _Recorder:
    """An `IngestionProgressStore` that remembers every report."""

    def __init__(self, *, broken: bool = False) -> None:
        self.reports: list[tuple[UUID, UUID, IngestionStage, int, str | None]] = []
        self._broken = broken

    async def report(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        stage: IngestionStage,
        percent: int,
        detail: str | None = None,
    ) -> None:
        if self._broken:
            raise ConnectionError("redis down")
        self.reports.append((tenant_id, document_id, stage, percent, detail))

    async def read_many(self, **kwargs: Any) -> dict[UUID, IngestionProgress]:
        return {}


class _Chunker:
    def __init__(self, n: int) -> None:
        self._n = n

    def chunk(self, blocks: list[ParsedBlock]) -> list[TextChunk]:
        return [TextChunk(text=f"passage {i}", token_count=10, source_location=None) for i in range(self._n)]


class _CountingEmbeddings(_FakeEmbeddings):
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def embed_batch(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.calls.append(len(texts))
        return await super().embed_batch(texts)


async def _index(n: int, recorder: _Recorder | None) -> tuple[_CountingEmbeddings, int]:
    embeddings = _CountingEmbeddings()

    async def progress(stage: IngestionStage, percent: int, detail: str | None) -> None:
        assert recorder is not None
        await recorder.report(
            tenant_id=TENANT_ID, document_id=DOCUMENT_ID, stage=stage, percent=percent, detail=detail
        )

    count = await index_blocks(
        _FakeSession(),  # type: ignore[arg-type]
        target=IndexingTarget(
            tenant_id=TENANT_ID, knowledge_base_id=uuid4(), document_id=DOCUMENT_ID, vector_namespace="ns"
        ),
        blocks=[ParsedBlock("x", None)],
        chunker=_Chunker(n),  # type: ignore[arg-type]
        embedding_client=embeddings,
        vector_search=_FakeVectorSearch(),  # type: ignore[arg-type]
        progress=progress if recorder is not None else None,
    )
    return embeddings, count


class TestTheWorkerReportsEachStage:
    async def test_a_document_goes_through_every_stage_in_order(self) -> None:
        recorder = _Recorder()
        await process_document_upload(
            _factory(_FakeSession()),
            _dependencies(progress=recorder),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )
        stages = [r[2] for r in recorder.reports]
        assert stages[0] is IngestionStage.EXTRACTING
        assert stages[1] is IngestionStage.CHUNKING
        assert IngestionStage.EMBEDDING in stages
        assert stages[-1] is IngestionStage.INDEXING
        # Every report is for this tenant's document.
        assert {(r[0], r[1]) for r in recorder.reports} == {(TENANT_ID, DOCUMENT_ID)}

    async def test_the_percentage_only_rises(self) -> None:
        recorder = _Recorder()
        await _index(200, recorder)
        percents = [r[3] for r in recorder.reports]
        assert percents == sorted(percents)
        assert percents[0] > 0 and percents[-1] < 100  # 100 is "ready", on the row

    async def test_embedding_advances_slice_by_slice(self) -> None:
        recorder = _Recorder()
        embeddings, count = await _index(EMBED_SLICE * 3 + 5, recorder)
        assert count == EMBED_SLICE * 3 + 5
        assert embeddings.calls == [EMBED_SLICE, EMBED_SLICE, EMBED_SLICE, 5]
        details = [r[4] for r in recorder.reports if r[2] is IngestionStage.EMBEDDING]
        assert details == [
            f"{done} of {count} passages" for done in (0, EMBED_SLICE, EMBED_SLICE * 2, EMBED_SLICE * 3)
        ]

    async def test_without_a_listener_embedding_is_one_request_as_before(self) -> None:
        embeddings, _ = await _index(EMBED_SLICE * 3, None)
        assert embeddings.calls == [EMBED_SLICE * 3]


class TestReportingNeverBreaksIngestion:
    async def test_a_broken_progress_store_still_leaves_the_document_ready(self) -> None:
        session = _FakeSession()
        await process_document_upload(
            _factory(session),
            _dependencies(progress=_Recorder(broken=True)),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )
        assert any("status = 'ready'" in s for s in session.statements)
        assert not any("status = 'failed'" in s for s in session.statements)


class _Reader:
    def __init__(self, found: dict[UUID, IngestionProgress]) -> None:
        self.found = found
        self.asked: dict[str, Any] = {}

    async def report(self, **kwargs: Any) -> None: ...

    async def read_many(self, *, tenant_id: UUID, document_ids: list[UUID]) -> dict[UUID, IngestionProgress]:
        self.asked = {"tenant_id": tenant_id, "document_ids": list(document_ids)}
        return {d: p for d, p in self.found.items() if d in document_ids}


def _document(kb_id: UUID, tenant_id: UUID, status: DocumentStatus) -> Document:
    return Document(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=kb_id,
        uploaded_by_membership_id=uuid4(),
        filename=f"{status.value}.pdf",
        content_type="application/pdf",
        storage_path=f"t/{uuid4()}",
        checksum="x",
        size_bytes=10,
        status=status,
        created_at=NOW,
    )


class TestTheListCarriesProgress:
    async def _list(self, reader: _Reader | None) -> tuple[list[Any], dict[str, Document], UUID]:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        docs = {s.value: _document(kb.id, tenant_id, s) for s in DocumentStatus}
        for d in docs.values():
            uow.documents.by_id[d.id] = d
        if reader is not None:
            reader.found = {
                d.id: IngestionProgress(stage=IngestionStage.EMBEDDING, percent=60, detail="3 of 5")
                for d in docs.values()
            }
        result = await ListDocuments(uow, reader).execute(  # type: ignore[arg-type]
            ListDocumentsQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                permissions=frozenset({QUERY}),
            )
        )
        return result, docs, tenant_id

    async def test_in_flight_and_failed_documents_get_their_stage(self) -> None:
        reader = _Reader({})
        result, docs, tenant_id = await self._list(reader)
        by_status = {s.document.status: s for s in result}
        assert by_status[DocumentStatus.PROCESSING].progress is not None
        assert by_status[DocumentStatus.FAILED].progress is not None
        assert by_status[DocumentStatus.READY].progress is None
        # Asked only about this caller's tenant, and only for listed ids.
        assert reader.asked["tenant_id"] == tenant_id
        assert set(reader.asked["document_ids"]) == {docs["processing"].id, docs["failed"].id}

    async def test_without_a_store_the_list_is_unchanged(self) -> None:
        result, _, _ = await self._list(None)
        assert all(s.progress is None for s in result)


class _FakeRedis:
    def __init__(self, *, broken: bool = False) -> None:
        self.data: dict[str, str] = {}
        self._broken = broken

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self._broken:
            raise ConnectionError
        self.data[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        if self._broken:
            raise ConnectionError
        return [self.data.get(k) for k in keys]


class TestTheRedisStore:
    async def test_a_report_reads_back(self) -> None:
        store = RedisIngestionProgressStore(_FakeRedis())  # type: ignore[arg-type]
        await store.report(
            tenant_id=TENANT_ID,
            document_id=DOCUMENT_ID,
            stage=IngestionStage.INDEXING,
            percent=90,
            detail="Saving 5 passages",
        )
        found = await store.read_many(tenant_id=TENANT_ID, document_ids=[DOCUMENT_ID])
        assert found[DOCUMENT_ID] == IngestionProgress(IngestionStage.INDEXING, 90, "Saving 5 passages")

    async def test_another_tenant_cannot_read_it(self) -> None:
        store = RedisIngestionProgressStore(_FakeRedis())  # type: ignore[arg-type]
        await store.report(
            tenant_id=TENANT_ID, document_id=DOCUMENT_ID, stage=IngestionStage.CHUNKING, percent=30
        )
        assert await store.read_many(tenant_id=uuid4(), document_ids=[DOCUMENT_ID]) == {}

    async def test_an_unreadable_value_is_skipped(self) -> None:
        redis = _FakeRedis()
        store = RedisIngestionProgressStore(redis)  # type: ignore[arg-type]
        other = uuid4()
        await store.report(tenant_id=TENANT_ID, document_id=other, stage=IngestionStage.CHUNKING, percent=30)
        redis.data[f"ingest-progress:{TENANT_ID}:{DOCUMENT_ID}"] = json.dumps({"stage": "bogus"})
        found = await store.read_many(tenant_id=TENANT_ID, document_ids=[DOCUMENT_ID, other])
        assert list(found) == [other]

    async def test_both_sides_fail_open(self) -> None:
        store = RedisIngestionProgressStore(_FakeRedis(broken=True))  # type: ignore[arg-type]
        await store.report(
            tenant_id=TENANT_ID, document_id=DOCUMENT_ID, stage=IngestionStage.CHUNKING, percent=30
        )
        assert await store.read_many(tenant_id=TENANT_ID, document_ids=[DOCUMENT_ID]) == {}
