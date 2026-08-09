"""Structured parsers, the dispatcher, and token-aware chunking.

Docling is not exercised here -- it loads ML models and is an integration
concern; what *is* tested is the markdown→blocks conversion it feeds, which is
this project's own logic.
"""

from __future__ import annotations

import json
import os

import pytest

from iam_platform.application.ai_resources.exceptions import (
    DocumentParseError,
    UnsupportedDocumentTypeError,
)
from iam_platform.application.ai_resources.ports import ParsedBlock
from iam_platform.core.config import IngestionSettings
from iam_platform.infrastructure.parsing import rich_documents
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.infrastructure.parsing.dispatcher import ParserDispatcher
from iam_platform.infrastructure.parsing.rich_documents import (
    DoclingDocumentParser,
    _markdown_to_blocks,
)
from iam_platform.infrastructure.parsing.structured import CsvParser, JsonParser, XmlParser

pytestmark = pytest.mark.unit


class TestCsvParser:
    async def test_rows_become_labelled_blocks(self) -> None:
        """Values are rendered as "column: value" -- a bare "2019" is
        meaningless to an embedding, "founded: 2019" is not."""
        data = b"name,founded\nAcme,1999\nGlobex,2019\n"

        blocks = await CsvParser().parse(data=data, content_type="text/csv", filename="c.csv")

        assert [b.text for b in blocks] == [
            "name: Acme; founded: 1999",
            "name: Globex; founded: 2019",
        ]

    async def test_source_location_points_at_the_spreadsheet_row(self) -> None:
        """Row 1 is the header, so the first data row must be row 2 -- an
        off-by-one here sends every citation to the wrong line."""
        data = b"name\nAcme\nGlobex\n"

        blocks = await CsvParser().parse(data=data, content_type="text/csv", filename="c.csv")

        assert [b.source_location for b in blocks] == ["row 2", "row 3"]

    async def test_tab_separated_files_are_not_mangled_into_one_column(self) -> None:
        data = b"name\tfounded\nAcme\t1999\n"

        blocks = await CsvParser().parse(data=data, content_type="text/csv", filename="c.tsv")

        assert blocks[0].text == "name: Acme; founded: 1999"

    async def test_utf8_bom_is_tolerated(self) -> None:
        """Spreadsheet exports routinely carry a BOM; rejecting them would
        reject a large share of real-world CSV."""
        data = "name,city\nAcme,Zürich\n".encode("utf-8-sig")

        blocks = await CsvParser().parse(data=data, content_type="text/csv", filename="c.csv")

        assert "Zürich" in blocks[0].text
        assert "name" in blocks[0].text  # BOM did not corrupt the first header

    async def test_empty_file_yields_no_blocks(self) -> None:
        blocks = await CsvParser().parse(data=b"", content_type="text/csv", filename="c.csv")
        assert blocks == []

    async def test_blank_cells_are_omitted_rather_than_rendered_empty(self) -> None:
        data = b"name,note\nAcme,\n"

        blocks = await CsvParser().parse(data=data, content_type="text/csv", filename="c.csv")

        assert blocks[0].text == "name: Acme"


class TestJsonParser:
    async def test_scalar_fields_of_an_object_stay_together(self) -> None:
        """Splitting one object into a block per key would destroy the
        association between fields that makes the record meaningful."""
        data = json.dumps({"name": "Ada", "role": "Engineer"}).encode()

        blocks = await JsonParser().parse(
            data=data, content_type="application/json", filename="d.json"
        )

        assert blocks[0].text == "name: Ada; role: Engineer"

    async def test_nested_structures_carry_a_path_as_source_location(self) -> None:
        data = json.dumps({"team": {"lead": {"name": "Ada"}}}).encode()

        blocks = await JsonParser().parse(
            data=data, content_type="application/json", filename="d.json"
        )

        assert blocks[0].source_location == "$.team.lead"

    async def test_arrays_are_indexed_in_the_path(self) -> None:
        data = json.dumps({"people": [{"name": "Ada"}, {"name": "Alan"}]}).encode()

        blocks = await JsonParser().parse(
            data=data, content_type="application/json", filename="d.json"
        )

        assert [b.source_location for b in blocks] == ["$.people[0]", "$.people[1]"]

    async def test_invalid_json_raises_a_tenant_readable_error(self) -> None:
        with pytest.raises(DocumentParseError, match="invalid JSON"):
            await JsonParser().parse(
                data=b"{not json", content_type="application/json", filename="d.json"
            )


class TestXmlParser:
    async def test_elements_become_blocks_with_their_tag(self) -> None:
        data = b"<catalog><book><title>Dune</title></book></catalog>"

        blocks = await XmlParser().parse(
            data=data, content_type="application/xml", filename="d.xml"
        )

        assert blocks[0].text == "title: Dune"
        assert blocks[0].source_location == "title"

    async def test_attributes_are_included(self) -> None:
        data = b'<catalog><book id="42">Dune</book></catalog>'

        blocks = await XmlParser().parse(
            data=data, content_type="application/xml", filename="d.xml"
        )

        assert "id=42" in blocks[0].text

    async def test_invalid_xml_raises_a_tenant_readable_error(self) -> None:
        with pytest.raises(DocumentParseError, match="invalid XML"):
            await XmlParser().parse(
                data=b"<unclosed>", content_type="application/xml", filename="d.xml"
            )


class TestParserDispatcher:
    async def test_structured_parsers_win_over_docling_for_their_formats(self) -> None:
        """Order matters: docling claims broadly, and a CSV run through layout
        reconstruction loses the row structure that makes "row 2" a citation."""
        dispatcher = ParserDispatcher()

        blocks = await dispatcher.parse(
            data=b"name\nAcme\n", content_type="text/csv", filename="c.csv"
        )

        assert blocks[0].source_location == "row 2"

    async def test_unsupported_type_is_refused_distinctly_from_a_parse_failure(self) -> None:
        """A valid file in an unreadable format is a different problem, and a
        different fix, from a corrupt file."""
        dispatcher = ParserDispatcher()

        with pytest.raises(UnsupportedDocumentTypeError):
            await dispatcher.parse(
                data=b"\x00\x01", content_type="application/x-elf", filename="a.bin"
            )

    def test_supports_reflects_the_union_of_its_parsers(self) -> None:
        dispatcher = ParserDispatcher()

        assert dispatcher.supports(content_type="text/csv", filename="a.csv")
        assert dispatcher.supports(content_type="application/pdf", filename="a.pdf")
        assert not dispatcher.supports(content_type="application/x-elf", filename="a.bin")


class TestMarkdownToBlocks:
    def test_headings_become_source_locations(self) -> None:
        markdown = "# Intro\nHello there.\n\n## Details\nMore text."

        blocks = _markdown_to_blocks(markdown)

        assert [(b.text, b.source_location) for b in blocks] == [
            ("Hello there.", "Intro"),
            ("More text.", "Details"),
        ]

    def test_a_heading_closes_the_previous_block(self) -> None:
        """Text under a new heading is a new topic; merging across that
        boundary produces chunks that answer the wrong question."""
        markdown = "# A\nfirst\n# B\nsecond"

        blocks = _markdown_to_blocks(markdown)

        assert len(blocks) == 2
        assert blocks[0].text == "first"


class _FakeEncoder:
    """One token per whitespace-separated word -- makes token maths readable
    in assertions without depending on tiktoken's real vocabulary."""

    def encode(self, text: str) -> list[int]:
        return [len(word) for word in text.split()]

    def decode(self, tokens: list[int]) -> str:
        return " ".join("w" * max(1, t) for t in tokens)


def _chunker(**overrides: int) -> TokenAwareChunker:
    settings = IngestionSettings(**overrides)  # type: ignore[arg-type]
    return TokenAwareChunker(settings, encoder=_FakeEncoder())


class TestTokenAwareChunker:
    def test_short_text_stays_one_chunk(self) -> None:
        chunker = _chunker(chunk_tokens=10, chunk_overlap_tokens=2)

        chunks = chunker.chunk([ParsedBlock(text="one two three", source_location="p1")])

        assert len(chunks) == 1
        assert chunks[0].source_location == "p1"

    def test_long_text_is_split_into_bounded_chunks(self) -> None:
        chunker = _chunker(chunk_tokens=5, chunk_overlap_tokens=1)
        text = " ".join(f"word{i}" for i in range(20))

        chunks = chunker.chunk([ParsedBlock(text=text, source_location=None)])

        assert len(chunks) > 1
        assert all(c.token_count <= 5 for c in chunks)

    def test_consecutive_blocks_sharing_a_location_are_merged(self) -> None:
        """Otherwise a page of short paragraphs yields a chunk per paragraph."""
        chunker = _chunker(chunk_tokens=100, chunk_overlap_tokens=10)

        chunks = chunker.chunk(
            [
                ParsedBlock(text="first para", source_location="page 1"),
                ParsedBlock(text="second para", source_location="page 1"),
            ]
        )

        assert len(chunks) == 1

    def test_blocks_are_never_merged_across_different_locations(self) -> None:
        """The property that keeps citations honest: a chunk attributed to
        page 7 must not contain a sentence from page 8."""
        chunker = _chunker(chunk_tokens=100, chunk_overlap_tokens=10)

        chunks = chunker.chunk(
            [
                ParsedBlock(text="from seven", source_location="page 7"),
                ParsedBlock(text="from eight", source_location="page 8"),
            ]
        )

        assert len(chunks) == 2
        assert chunks[0].source_location == "page 7"
        assert "eight" not in chunks[0].text

    def test_non_consecutive_blocks_with_the_same_location_are_not_joined(self) -> None:
        """Two passages that merely share a heading are still two passages;
        joining them would fabricate adjacency the source never had."""
        chunker = _chunker(chunk_tokens=100, chunk_overlap_tokens=10)

        chunks = chunker.chunk(
            [
                ParsedBlock(text="alpha", source_location="Intro"),
                ParsedBlock(text="beta", source_location="Body"),
                ParsedBlock(text="gamma", source_location="Intro"),
            ]
        )

        assert len(chunks) == 3

    def test_empty_blocks_are_dropped(self) -> None:
        chunker = _chunker(chunk_tokens=100, chunk_overlap_tokens=10)

        assert chunker.chunk([ParsedBlock(text="   ", source_location=None)]) == []

    def test_overlap_not_smaller_than_chunk_size_is_refused(self) -> None:
        """A non-advancing window would loop forever rather than fail."""
        chunker = _chunker(chunk_tokens=5, chunk_overlap_tokens=5)
        text = " ".join(f"word{i}" for i in range(20))

        with pytest.raises(ValueError, match="must be smaller than"):
            chunker.chunk([ParsedBlock(text=text, source_location=None)])


class TestDoclingRuntimeCompilationGuard:
    """Docling's layout model runs through TorchInductor, which generates and
    compiles C++ *at parse time*. Phase 9's hardened runtime image ships no
    compiler on purpose, so without this guard every PDF/DOCX/XLSX/PPTX/image
    fails in production with `InvalidCxxCompiler` while CSV/JSON/XML keep
    working -- a partial failure that reads like a bad file rather than a bad
    deployment. Found by pushing a real PDF through a real worker; no test
    that stubs the converter can see it.
    """

    def test_guard_runs_before_docling_is_imported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Order is the whole point: torch reads the variable at import time,
        so setting it after `import docling` would be a no-op."""

        class _GuardRan(Exception):
            pass

        def _explode() -> None:
            raise _GuardRan

        monkeypatch.setattr(rich_documents, "_disable_torch_compilation", _explode)

        with pytest.raises(_GuardRan):
            DoclingDocumentParser()._get_converter()

    def test_guard_sets_the_variable_torch_actually_honours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`TORCH_COMPILE_DISABLE` is the one that works on torch 2.x.
        `TORCHDYNAMO_DISABLE` is the obsolete name most search results still
        give and is silently ignored -- asserting only on it would let a
        non-fix pass as a fix, which is how this nearly shipped broken."""
        monkeypatch.delenv("TORCH_COMPILE_DISABLE", raising=False)
        monkeypatch.delenv("TORCHDYNAMO_DISABLE", raising=False)

        rich_documents._disable_torch_compilation()

        assert os.environ["TORCH_COMPILE_DISABLE"] == "1"
        assert os.environ["TORCHDYNAMO_DISABLE"] == "1"

    def test_guard_respects_an_operator_who_opted_back_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator whose image *does* carry a toolchain can export the
        variable to get the compiled (faster) path back. `setdefault` must not
        stomp on that."""
        monkeypatch.setenv("TORCH_COMPILE_DISABLE", "0")

        rich_documents._disable_torch_compilation()

        assert os.environ["TORCH_COMPILE_DISABLE"] == "0"
