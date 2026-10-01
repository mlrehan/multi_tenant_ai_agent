"""A new tenant's first uploads race to create its Qdrant collection.

Found live (2026-10-01): a brand-new tenant uploaded five files, the worker ran
them concurrently, every job saw no collection, and the losers of the create
were refused with 409 -- so three of five first documents were marked
"failed" with a reason nobody could act on. Losing that race must be success.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from qdrant_client.http.exceptions import UnexpectedResponse

from iam_platform.infrastructure.vector.qdrant_search import QdrantVectorSearchClient


class _RacingClient:
    """Reports no collection, then refuses the create as a racing peer would."""

    def __init__(self, create_status: int | None) -> None:
        self.create_status = create_status
        self.indexes: list[str] = []

    async def collection_exists(self, name: str) -> bool:
        return False

    async def create_collection(self, **kwargs: Any) -> None:
        if self.create_status is not None:
            raise UnexpectedResponse(self.create_status, "refused", b"{}", httpx.Headers())

    async def create_payload_index(self, *, field_name: str, **kwargs: Any) -> None:
        self.indexes.append(field_name)


def _store(client: _RacingClient) -> QdrantVectorSearchClient:
    return QdrantVectorSearchClient(settings=None, embedding_client=None, client=client)  # type: ignore[arg-type]


def _namespace() -> str:
    return f"{uuid4()}/{uuid4()}"


async def test_losing_the_create_race_is_success_and_still_indexes() -> None:
    client = _RacingClient(create_status=409)
    await _store(client).ensure_namespace(namespace=_namespace(), dimensions=8)
    assert client.indexes == ["knowledge_base_id", "document_id"]


async def test_winning_the_race_indexes_as_before() -> None:
    client = _RacingClient(create_status=None)
    await _store(client).ensure_namespace(namespace=_namespace(), dimensions=8)
    assert client.indexes == ["knowledge_base_id", "document_id"]


async def test_any_other_refusal_still_fails_the_job() -> None:
    client = _RacingClient(create_status=500)
    with pytest.raises(UnexpectedResponse):
        await _store(client).ensure_namespace(namespace=_namespace(), dimensions=8)
    assert client.indexes == []
