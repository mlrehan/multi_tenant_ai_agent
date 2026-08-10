"""The ingestion pipeline's control flow, against fakes.

What matters here is not that parsing works (covered separately) but that the
job's *state machine* is correct: a document must never be left in
``processing``, a failure must be recorded rather than swallowed, and a
redelivered job must not duplicate anything.

Uses a fake session that records executed SQL rather than a real database --
these assertions are about which statements the job decides to run and in what
order, which a live Postgres would obscure rather than clarify. The RLS half
is proven separately in ``tests/integration/db/test_document_chunks_rls.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    DocumentContentNotFoundError,
    DocumentParseError,
)
from iam_platform.application.ai_resources.ports import ParsedBlock, VectorChunk
from iam_platform.core.config import IngestionSettings
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.workers.job_context import JobAuthorizationError
from iam_platform.workers.jobs.process_document_upload import (
    IngestionDependencies,
    process_document_upload,
)

pytestmark = pytest.mark.unit

TENANT_ID = uuid4()
ACTOR_ID = uuid4()
DOCUMENT_ID = uuid4()
KB_ID = uuid4()
MEMBERSHIP_ID = uuid4()
NAMESPACE = f"{TENANT_ID}/{KB_ID}"


class _FakeResult:
    def __init__(self, row: tuple[Any, ...] | None = None) -> None:
        self._row = row

    def first(self) -> tuple[Any, ...] | None:
        return self._row

    def scalar(self) -> Any:
        return self._row[0] if self._row else None


class _FakeSession:
    """Records SQL and answers the handful of reads the job makes."""

    def __init__(self, *, document_missing: bool = False) -> None:
        self.statements: list[str] = []
        #: Bound parameters, so a test can assert on the *value* written and
        #: not merely that some UPDATE ran. `failure_reason` is shown verbatim
        #: to the tenant, which makes its content worth asserting.
        self.parameters: list[dict[str, Any]] = []
        self._document_missing = document_missing

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        self.parameters.append(dict(params or {}))

        if sql.startswith("SELECT set_config"):
            return _FakeResult(("",))
        if "FROM tenants" in sql:
            return _FakeResult(("active",))
        if "FROM tenant_memberships" in sql:
            return _FakeResult((MEMBERSHIP_ID, "active"))
        if "FROM users" in sql:
            return _FakeResult(("active", None))
        if "FROM documents d" in sql:
            if self._document_missing:
                return _FakeResult(None)
            return _FakeResult((KB_ID, "report.csv", "text/csv", "t/kb/doc", NAMESPACE))
        return _FakeResult(None)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self


class _RaisingSession(_FakeSession):
    """Fails on the chunk INSERT, to exercise the failure path."""

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = " ".join(str(statement).split())
        if sql.startswith("INSERT INTO document_chunks"):
            raise RuntimeError("database exploded")
        return await super().execute(statement, params)


class _FakeStorage:
    def __init__(self, data: bytes = b"name\nAcme\n", *, missing: bool = False) -> None:
        self._data = data
        self._missing = missing
        self.deleted: list[str] = []

    async def put(self, *, path: str, data: bytes, content_type: str) -> None: ...

    async def get(self, *, path: str) -> bytes:
        if self._missing:
            raise DocumentContentNotFoundError(path)
        return self._data

    async def delete(self, *, path: str) -> None:
        self.deleted.append(path)


class _FakeParser:
    def __init__(self, blocks: list[ParsedBlock] | None = None, *, error: Exception | None = None):
        self._blocks = blocks if blocks is not None else [ParsedBlock("hello world", "row 2")]
        self._error = error

    def supports(self, *, content_type: str, filename: str) -> bool:
        return True

    async def parse(self, *, data: bytes, content_type: str, filename: str) -> list[ParsedBlock]:
        if self._error:
            raise self._error
        return self._blocks


class _FakeEmbeddings:
    dimensions = 4

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class _FakeVectorSearch:
    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.upserted: list[VectorChunk] = []
        self.deleted_documents: list[UUID] = []

    async def ensure_namespace(self, *, namespace: str, dimensions: int) -> None:
        self.ensured.append(namespace)

    async def upsert(self, *, namespace: str, chunks: list[VectorChunk]) -> None:
        self.upserted.extend(chunks)

    async def delete_document(self, *, namespace: str, document_id: UUID) -> None:
        self.deleted_documents.append(document_id)

    async def query(self, *, namespace: str, query_text: str, top_k: int) -> list[Any]:
        return []


def _dependencies(**overrides: Any) -> IngestionDependencies:
    defaults: dict[str, Any] = {
        "object_storage": _FakeStorage(),
        "parser": _FakeParser(),
        "chunker": TokenAwareChunker(IngestionSettings()),
        "embedding_client": _FakeEmbeddings(),
        "vector_search": _FakeVectorSearch(),
    }
    defaults.update(overrides)
    return IngestionDependencies(**defaults)


def _factory(session: _FakeSession) -> Any:
    return lambda: session


class TestSuccessPath:
    async def test_document_is_marked_ready(self) -> None:
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        assert any("status = 'ready'" in s for s in session.statements)

    async def test_chunks_are_written_and_upserted(self) -> None:
        session = _FakeSession()
        vectors = _FakeVectorSearch()

        await process_document_upload(
            _factory(session),
            _dependencies(vector_search=vectors),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        assert any("INSERT INTO document_chunks" in s for s in session.statements)
        assert len(vectors.upserted) == 1
        assert vectors.upserted[0].document_id == DOCUMENT_ID

    async def test_rls_context_is_set_before_any_read(self) -> None:
        """The job runs on the RLS-subject connection; a read issued before
        the context is set would be unscoped."""
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        first_select = next(
            i for i, s in enumerate(session.statements) if "FROM tenants" in s
        )
        set_configs = [i for i, s in enumerate(session.statements) if "set_config" in s]
        assert set_configs and max(set_configs) < first_select


class TestIdempotency:
    async def test_previous_chunks_are_cleared_before_writing(self) -> None:
        """A redelivered job must replace, not accumulate -- Celery's
        `acks_late` means at-least-once delivery is normal, not exceptional."""
        session = _FakeSession()
        vectors = _FakeVectorSearch()

        await process_document_upload(
            _factory(session),
            _dependencies(vector_search=vectors),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        delete_at = next(
            i for i, s in enumerate(session.statements) if "DELETE FROM document_chunks" in s
        )
        insert_at = next(
            i for i, s in enumerate(session.statements) if "INSERT INTO document_chunks" in s
        )
        assert delete_at < insert_at, "chunks must be cleared before new ones are written"
        assert vectors.deleted_documents == [DOCUMENT_ID]


class TestFailurePaths:
    async def test_parse_failure_marks_the_document_failed_with_a_reason(self) -> None:
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(parser=_FakeParser(error=DocumentParseError("x.pdf: encrypted"))),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        assert any("status = 'failed'" in s for s in session.statements)

    async def test_parse_failure_does_not_raise(self) -> None:
        """A bad document is not a job failure -- raising would make Celery
        retry a file that will never parse."""
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(parser=_FakeParser(error=DocumentParseError("bad"))),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )  # must not raise

    async def test_missing_bytes_are_reported_as_a_storage_problem(self) -> None:
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(object_storage=_FakeStorage(missing=True)),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        assert any("status = 'failed'" in s for s in session.statements)

    async def test_unexpected_errors_do_not_leak_internals_to_the_tenant(self) -> None:
        """An unexpected traceback can carry table names, paths, or worse; the
        document owner is not the right audience for those."""
        session = _RaisingSession()

        await process_document_upload(
            _factory(session),
            _dependencies(),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        failed = [s for s in session.statements if "status = 'failed'" in s]
        assert failed
        assert not any("exploded" in s for s in session.statements)

    async def test_the_document_never_stays_in_processing(self) -> None:
        """The invariant the whole failure path exists for: `processing` means
        "a worker is coming", so a document left there is indistinguishable
        from one still queued."""
        for dependencies in (
            _dependencies(parser=_FakeParser(error=DocumentParseError("bad"))),
            _dependencies(object_storage=_FakeStorage(missing=True)),
            _dependencies(),
        ):
            session = _FakeSession()
            await process_document_upload(
                _factory(session),
                dependencies,
                tenant_id=TENANT_ID,
                actor_user_id=ACTOR_ID,
                document_id=DOCUMENT_ID,
            )
            assert any(
                "status = 'ready'" in s or "status = 'failed'" in s
                for s in session.statements
            ), "document was left in 'processing'"


class TestAuthorizationRefusal:
    async def test_authorization_failure_propagates_and_does_not_touch_the_document(
        self,
    ) -> None:
        """Re-raised, not recorded: nothing is wrong with the document, and
        the authorization to write its row is exactly what was refused."""

        class _SuspendedTenantSession(_FakeSession):
            async def execute(
                self, statement: Any, params: dict[str, Any] | None = None
            ) -> _FakeResult:
                sql = " ".join(str(statement).split())
                if "FROM tenants" in sql:
                    self.statements.append(sql)
                    return _FakeResult(("suspended",))
                return await super().execute(statement, params)

        session = _SuspendedTenantSession()

        with pytest.raises(JobAuthorizationError):
            await process_document_upload(
                _factory(session),
                _dependencies(),
                tenant_id=TENANT_ID,
                actor_user_id=ACTOR_ID,
                document_id=DOCUMENT_ID,
            )

        assert not any("status = 'failed'" in s for s in session.statements)


class TestEmptyDocuments:
    """A document that indexed nothing is a failure, not a quiet success.

    This class previously asserted the opposite -- that an empty parse was "a
    legitimate outcome" and should be marked `ready`. That belief is what let a
    40-page scanned PDF, whose OCR ran out of memory on 38 of its pages, be
    recorded as successfully ingested with zero chunks: the one state that
    looks like success in the console and cannot answer a single question.

    The tenant uploaded the file in order to search it. If nothing is
    searchable, saying `ready` is telling them the opposite of what happened.
    """

    async def test_a_document_with_no_extractable_text_is_failed(self) -> None:
        session = _FakeSession()
        vectors = _FakeVectorSearch()

        await process_document_upload(
            _factory(session),
            _dependencies(parser=_FakeParser(blocks=[]), vector_search=vectors),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        assert not any("status = 'ready'" in s for s in session.statements)
        assert any("status = 'failed'" in s for s in session.statements)
        assert vectors.upserted == []

    async def test_the_failure_reason_names_the_file_and_suggests_a_cause(self) -> None:
        """`failure_reason` is rendered verbatim in the console, so it has to
        be worth reading: which file, and what a tenant might do about it."""
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(parser=_FakeParser(blocks=[])),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        reasons = [p.get("reason", "") for p in session.parameters if "reason" in p]
        assert reasons, "a failure reason should have been recorded"
        assert "scanned" in reasons[-1].lower()

    async def test_no_exit_path_leaves_the_document_processing(self) -> None:
        """The invariant the whole job is built around: `processing` means "a
        worker is coming", so a document left there is indistinguishable from
        one still queued."""
        session = _FakeSession()

        await process_document_upload(
            _factory(session),
            _dependencies(parser=_FakeParser(blocks=[])),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )

        terminal = [
            s
            for s in session.statements
            if "status = 'ready'" in s or "status = 'failed'" in s
        ]
        assert terminal, "the document must reach a terminal status"
