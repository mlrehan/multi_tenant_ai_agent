"""Sources carry their document's title, and the public ones are grouped.

Five passages from one web page used to reach the reader as five identical
sources. Two things fix that: the answer path attaches each document's title
(best effort -- it must never cost an answer), and the public payload tells the
widget which passages share a document without naming anything internal.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from iam_platform.api.v1.public_chat.router import _public_citations
from iam_platform.application.ai_resources.answer_question import AnswerQuestion, Citation

TENANT = uuid4()


def _citation(label: str, doc: UUID, location: str | None = None) -> Citation:
    return Citation(
        label=label,
        document_id=doc,
        chunk_id=uuid4(),
        source_location=location,
        relevance=0.5,
    )


class _Docs:
    def __init__(self, described: dict[UUID, tuple[str, str | None]], *, fail: bool = False) -> None:
        self._described = described
        self._fail = fail
        self.calls: list[list[UUID]] = []

    async def describe_many(self, document_ids: list[UUID]) -> dict[UUID, tuple[str, str | None]]:
        self.calls.append(document_ids)
        if self._fail:
            raise RuntimeError("database unavailable")
        return {d: self._described[d] for d in document_ids if d in self._described}


class _Uow:
    def __init__(self, docs: _Docs) -> None:
        self.documents = docs

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _pipeline(docs: _Docs) -> AnswerQuestion:
    return AnswerQuestion(lambda _a, _t: _Uow(docs), None, None, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
class TestTitlesAreAttached:
    async def test_each_citation_gets_its_documents_title_and_url(self) -> None:
        page, upload = uuid4(), uuid4()
        docs = _Docs({page: ("Course List", "https://example.com/courses"), upload: ("fees.pdf", None)})
        cited = await _pipeline(docs)._with_titles(
            TENANT, [_citation("1", page), _citation("2", page), _citation("3", upload)]
        )
        assert [(c.title, c.source_url) for c in cited] == [
            ("Course List", "https://example.com/courses"),
            ("Course List", "https://example.com/courses"),
            ("fees.pdf", None),
        ]

    async def test_each_document_is_looked_up_once(self) -> None:
        """Five passages of one page are one lookup, not five."""
        page = uuid4()
        docs = _Docs({page: ("Course List", None)})
        await _pipeline(docs)._with_titles(TENANT, [_citation(str(i), page) for i in range(5)])
        assert docs.calls == [[page]]

    async def test_a_failed_lookup_never_costs_the_answer(self) -> None:
        """On the answer path: a broken lookup must mean "no titles", not an
        error in place of the reply."""
        page = uuid4()
        original = [_citation("1", page)]
        cited = await _pipeline(_Docs({}, fail=True))._with_titles(TENANT, original)
        assert cited == original
        assert cited[0].title is None

    async def test_no_tenant_means_no_lookup(self) -> None:
        docs = _Docs({})
        await _pipeline(docs)._with_titles(None, [_citation("1", uuid4())])
        assert docs.calls == []


class TestThePublicPayloadGroupsWithoutNaming:
    def test_passages_of_one_document_share_a_key(self) -> None:
        page, other = uuid4(), uuid4()
        out = _public_citations(
            [
                _with(_citation("1", page), "Course List", "https://example.com/c"),
                _with(_citation("2", other), "Fees", "https://example.com/f"),
                _with(_citation("3", page), "Course List", "https://example.com/c"),
            ]
        )
        assert [c["doc"] for c in out] == ["1", "2", "1"]

    def test_keys_follow_relevance_order(self) -> None:
        """First appearance is rank order, so card 1 is the best source."""
        a, b = uuid4(), uuid4()
        out = _public_citations([_citation("1", b), _citation("2", a)])
        assert [c["doc"] for c in out] == ["1", "2"]

    def test_no_internal_identifier_is_exposed(self) -> None:
        doc = uuid4()
        out = _public_citations([_citation("1", doc, "page 5")])
        assert str(doc) not in repr(out)

    def test_a_web_pages_title_is_shown(self) -> None:
        out = _public_citations([_with(_citation("1", uuid4()), "Course List", "https://example.com/c")])
        assert out[0]["kind"] == "web"
        assert out[0]["title"] == "Course List"
        assert out[0]["url"] == "https://example.com/c"

    def test_an_uploaded_files_name_is_withheld(self) -> None:
        """A filename can disclose internal material to a stranger; a public
        page's title cannot."""
        out = _public_citations(
            [_with(_citation("1", uuid4(), "page 5"), "Board minutes - confidential.pdf", None)]
        )
        assert out[0]["kind"] == "document"
        assert out[0]["title"] is None
        assert "confidential" not in repr(out)
        assert out[0]["source"] == "page 5"


def _with(c: Citation, title: str, url: str | None) -> Citation:
    from dataclasses import replace

    return replace(c, title=title, source_url=url)
