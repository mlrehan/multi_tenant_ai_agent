"""Routes a document to the first parser that claims it.

A composite implementing the same ``DocumentParser`` port as its members, so
callers depend on one thing rather than knowing the format matrix.

**Order matters.** Structured parsers come first because their claims are
narrow and exact (``.csv``, ``.json``, ``.xml``), while docling's are broad.
Reversing the order would let docling swallow formats it handles worse than
the purpose-built parser -- a CSV run through layout reconstruction loses the
row structure that makes "row 42" a usable citation.
"""

from __future__ import annotations

from iam_platform.application.ai_resources.exceptions import UnsupportedDocumentTypeError
from iam_platform.application.ai_resources.ports import DocumentParser, ParsedBlock
from iam_platform.infrastructure.parsing.rich_documents import DoclingDocumentParser
from iam_platform.infrastructure.parsing.structured import CsvParser, JsonParser, XmlParser


class ParserDispatcher:
    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        self._parsers: list[DocumentParser] = parsers or [
            CsvParser(),
            JsonParser(),
            XmlParser(),
            DoclingDocumentParser(),
        ]

    def supports(self, *, content_type: str, filename: str) -> bool:
        return any(
            parser.supports(content_type=content_type, filename=filename)
            for parser in self._parsers
        )

    async def parse(
        self, *, data: bytes, content_type: str, filename: str
    ) -> list[ParsedBlock]:
        for parser in self._parsers:
            if parser.supports(content_type=content_type, filename=filename):
                return await parser.parse(
                    data=data, content_type=content_type, filename=filename
                )
        raise UnsupportedDocumentTypeError(
            f"{filename}: no parser handles content type {content_type!r}"
        )
