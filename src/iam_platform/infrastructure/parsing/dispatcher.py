"""Chooses a parser from what the bytes actually are.

The rule, in one line: **use the cheapest parser that can read this format
well, and reserve the layout models for documents whose structure exists only
as pixels.**

That is not a performance preference. Docling reconstructs structure from a
rendered page, so running it over a DOCX throws away markup that already
states which run is a heading and which cell belongs to which column, then
guesses it back. The result is slower *and* worse. This package already
proved the point once for text-layer PDFs (`fast_pdf.py`, 1,351x); the same
reasoning covers every format whose structure is declared rather than drawn.

Routing therefore happens in three tiers:

1. **Declared structure** -- CSV, JSON, XML, HTML, EML, DOCX, PPTX, XLSX, and
   PDFs that carry a text layer. Read natively, keeping real provenance
   ("slide 7", "Sheet1 rows 41-80") that survives into citations.
2. **Drawn structure** -- scans, images, PDFs without a text layer, EPUB,
   OpenDocument, pre-2007 Office. Docling, with OCR.
3. **Ambiguous** -- an Office file whose native parse came back nearly empty
   while the file contains pictures. That is a scan pasted into Word, and it
   falls through to docling.

The format is decided by `detect_kind` from the file's own bytes, not from
the extension or the client's `Content-Type`, both of which the caller
controls. See `file_kind.py`.
"""

from __future__ import annotations

import logging
from typing import Any

from iam_platform.application.ai_resources.exceptions import (
    DocumentParseError,
    UnsupportedDocumentTypeError,
)
from iam_platform.application.ai_resources.ports import ParsedBlock
from iam_platform.core.config import IngestionSettings
from iam_platform.infrastructure.parsing.fast_pdf import FastPdfParser, ParserDeclined
from iam_platform.infrastructure.parsing.file_kind import (
    ZIP_CONTAINER_KINDS,
    ArchiveLimits,
    FileKind,
    UnsafeArchiveError,
    assert_archive_is_safe,
    detect_kind,
)
from iam_platform.infrastructure.parsing.markup import EmlParser, HtmlParser
from iam_platform.infrastructure.parsing.native_office import (
    NativeDocxParser,
    NativePptxParser,
    NativeXlsxParser,
)
from iam_platform.infrastructure.parsing.rich_documents import DoclingDocumentParser
from iam_platform.infrastructure.parsing.structured import CsvParser, JsonParser, XmlParser

logger = logging.getLogger("iam_platform.infrastructure.parsing.dispatcher")

#: Formats docling owns outright: their content is an image, or their
#: container is one this platform has no native reader for.
_DOCLING_KINDS = frozenset(
    {
        FileKind.IMAGE,
        FileKind.EPUB,
        FileKind.ODT,
        FileKind.ODS,
        FileKind.ODP,
        FileKind.LEGACY_OFFICE,
    }
)

#: Every format the upload endpoint accepts. `UNKNOWN` is absent on purpose:
#: a file whose type cannot be established is refused at upload with a 415
#: rather than speculatively fed to a parser.
SUPPORTED_KINDS = frozenset(
    {
        FileKind.PDF,
        FileKind.DOCX,
        FileKind.PPTX,
        FileKind.XLSX,
        FileKind.CSV,
        FileKind.TSV,
        FileKind.TEXT,
        FileKind.MARKDOWN,
        FileKind.HTML,
        FileKind.JSON,
        FileKind.JSONL,
        FileKind.XML,
        FileKind.EML,
        *_DOCLING_KINDS,
    }
)


class ParserDispatcher:
    def __init__(
        self,
        settings: IngestionSettings | None = None,
        *,
        docling: Any | None = None,
    ) -> None:
        """`docling` is the one injectable parser, and only for tests.

        It is the expensive one, so the properties worth asserting are all
        about *whether it ran* -- a text-native PDF must never reach it, a
        declined one must. Every other parser is cheap and deterministic and
        is exercised directly.
        """
        self._settings = settings or IngestionSettings()
        self._csv = CsvParser()
        self._json = JsonParser()
        self._xml = XmlParser()
        self._html = HtmlParser()
        self._eml = EmlParser()
        self._docx = NativeDocxParser()
        self._pptx = NativePptxParser()
        self._xlsx = NativeXlsxParser()
        self._fast_pdf = FastPdfParser()
        self._docling = docling if docling is not None else DoclingDocumentParser(self._settings)

    def supports(self, *, content_type: str, filename: str) -> bool:
        """Whether this filename *might* be supported.

        Extension-based, and deliberately so: the upload route calls this
        before the bytes are in hand, to reject an obvious `.exe` while the
        person is still at the keyboard. The authoritative decision is made
        from content in `parse`, which is why this is permissive.
        """
        lowered = filename.lower()
        return lowered.endswith(_ACCEPTED_EXTENSIONS) or (
            content_type.split(";")[0].strip().lower() in _ACCEPTED_TYPES
        )

    async def parse(
        self, *, data: bytes, content_type: str, filename: str
    ) -> list[ParsedBlock]:
        kind = detect_kind(data, filename)
        if kind is FileKind.UNKNOWN:
            raise UnsupportedDocumentTypeError(
                f"{filename}: the file's contents do not match a supported document "
                f"format (declared {content_type!r})"
            )
        if kind not in SUPPORTED_KINDS:
            raise UnsupportedDocumentTypeError(f"{filename}: {kind.value} is not supported")

        if kind in ZIP_CONTAINER_KINDS:
            # Before any parser opens it. A hostile archive is refused for
            # what it would cost, not for being malformed.
            try:
                assert_archive_is_safe(data, limits=self._archive_limits())
            except UnsafeArchiveError as exc:
                raise DocumentParseError(f"{filename}: {exc}") from exc

        if kind is not detect_kind(data, filename):  # pragma: no cover - defensive
            raise DocumentParseError(f"{filename}: file type changed during inspection")

        return await self._parse_by_kind(kind, data=data, content_type=content_type, filename=filename)

    def _archive_limits(self) -> ArchiveLimits:
        return ArchiveLimits(
            max_entries=self._settings.archive_max_entries,
            max_uncompressed_bytes=self._settings.archive_max_uncompressed_bytes,
        )

    async def _parse_by_kind(
        self, kind: FileKind, *, data: bytes, content_type: str, filename: str
    ) -> list[ParsedBlock]:
        if kind in (FileKind.CSV, FileKind.TSV):
            return await self._csv.parse(data=data, content_type=content_type, filename=filename)
        if kind in (FileKind.JSON, FileKind.JSONL):
            return await self._json.parse(data=data, content_type=content_type, filename=filename)
        if kind is FileKind.XML:
            return await self._xml.parse(data=data, content_type=content_type, filename=filename)
        if kind is FileKind.HTML:
            return await self._html.parse(data=data, filename=filename)
        if kind is FileKind.EML:
            return await self._eml.parse(data=data, filename=filename)
        if kind in (FileKind.TEXT, FileKind.MARKDOWN):
            return _plain_text_blocks(data)

        if kind is FileKind.PDF:
            try:
                # Claims every PDF, then declines the ones with no text layer
                # so docling's OCR gets them.
                return await self._fast_pdf.parse(
                    data=data, content_type=content_type, filename=filename
                )
            except ParserDeclined:
                return await self._docling.parse(
                    data=data, content_type=content_type, filename=filename
                )

        if kind is FileKind.DOCX:
            return await self._native_first(
                await self._docx.parse(data=data, filename=filename),
                images=self._docx.image_count(data),
                kind=kind,
                data=data,
                content_type=content_type,
                filename=filename,
            )
        if kind is FileKind.PPTX:
            return await self._native_first(
                await self._pptx.parse(data=data, filename=filename),
                images=self._pptx.picture_count(data),
                kind=kind,
                data=data,
                content_type=content_type,
                filename=filename,
            )
        if kind is FileKind.XLSX:
            # No fallback: a spreadsheet's content is its cells, and docling
            # reading a *rendered* spreadsheet is strictly worse. An empty
            # workbook is empty, not mis-parsed.
            return await self._xlsx.parse(data=data, filename=filename)

        return await self._docling.parse(
            data=data, content_type=content_type, filename=filename
        )

    async def _native_first(
        self,
        blocks: list[ParsedBlock],
        *,
        images: int,
        kind: FileKind,
        data: bytes,
        content_type: str,
        filename: str,
    ) -> list[ParsedBlock]:
        """Keep the native result unless it looks like a scan in a wrapper.

        Both conditions are required. Thin text alone is not enough -- a
        one-line memo is legitimately thin, and sending it to OCR would burn
        minutes to reproduce the same line. Pictures alone are not enough
        either: a well-written report with a chart in it parses perfectly.
        Together they describe the case the native reader genuinely cannot
        handle, which is a document whose words are inside the images.
        """
        characters = sum(len(block.text) for block in blocks)
        if characters >= self._settings.native_office_min_chars or images == 0:
            return blocks

        logger.info(
            "%s produced only %s characters natively and contains %s image(s); "
            "re-reading with OCR",
            filename,
            characters,
            images,
        )
        try:
            return await self._docling.parse(
                data=data, content_type=content_type, filename=filename
            )
        except DocumentParseError:
            # The native text, however little, beats nothing: docling failing
            # on a file the native reader could open is a reason to keep what
            # was read, not to discard it.
            if blocks:
                logger.warning("OCR fallback failed for %s; keeping the native text", filename)
                return blocks
            raise


def _plain_text_blocks(data: bytes) -> list[ParsedBlock]:
    """Plain text and markdown, split on blank lines.

    Paragraph-level blocks rather than one block for the file: the chunker can
    merge, but it never splits across a `source_location` boundary, so
    starting coarse would throw away the only structure these formats have.
    """
    from iam_platform.infrastructure.parsing.markup import decode_text

    text = decode_text(data)
    blocks: list[ParsedBlock] = []
    for paragraph in text.split("\n\n"):
        cleaned = paragraph.strip()
        if cleaned:
            blocks.append(ParsedBlock(text=cleaned, source_location=None))
    return blocks


_ACCEPTED_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".csv",
    ".tsv",
    ".txt",
    ".text",
    ".log",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".ndjson",
    ".xml",
    ".html",
    ".htm",
    ".eml",
    ".epub",
    ".odt",
    ".ods",
    ".odp",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
)

_ACCEPTED_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/epub+zip",
        "text/csv",
        "text/tab-separated-values",
        "text/plain",
        "text/markdown",
        "text/html",
        "message/rfc822",
        "application/json",
        "application/xml",
        "text/xml",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "image/webp",
    }
)
