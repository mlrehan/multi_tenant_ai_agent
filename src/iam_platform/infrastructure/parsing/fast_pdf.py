"""Text-layer-first PDF extraction, with docling as the fallback.

**Why this exists.** Most business PDFs are *text-native*: exported from Word,
Google Docs or a reporting tool, carrying an embedded text layer that is the
author's own text, exactly. Reading that layer is a parse, not an inference --
microseconds to milliseconds, and byte-accurate.

Docling instead runs ML layout models (DocLayNet, TableFormer) through torch
over a rendered image of every page. That is the right tool for a *scanned*
document, where there is no text layer and the words must be recovered from
pixels. Used on a text-native PDF it is orders of magnitude slower and
strictly less accurate -- inferring back the text that was sitting there all
along. This deployment makes it slower still, because the hardened image ships
no C++ compiler and therefore sets ``TORCH_COMPILE_DISABLE=1``, forcing eager
execution.

So: try the text layer first; hand over to docling only when there isn't one.

**pypdfium2, not PyMuPDF.** PyMuPDF is the better-known choice and is AGPL-3.0
or paid commercial -- network copyleft, which is a real problem for a hosted
multi-tenant product. pypdfium2 wraps PDFium (the engine in Chrome), is
BSD-3-Clause/Apache-2.0, and is *already* an indirect dependency via docling,
so this adds no new supply chain at all.

**Citations get better, not just faster.** Page numbers come free from the text
layer, so a passage can cite "page 7". The docling path falls back to headings
because it reports pages inconsistently across formats.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from iam_platform.application.ai_resources.exceptions import DocumentParseError
from iam_platform.application.ai_resources.ports import ParsedBlock
from iam_platform.core.config import IngestionSettings

logger = logging.getLogger("iam_platform.infrastructure.parsing.fast_pdf")

_PDF_TYPES = {"application/pdf", "application/x-pdf"}


class ParserDeclined(Exception):
    """A parser claimed the format, then looked at the bytes and backed out.

    Distinct from `DocumentParseError`, which means "this file is broken".
    Declining means "this file is fine, but another parser will do it better"
    -- so the dispatcher must keep going rather than fail the document.
    """


class FastPdfParser:
    """Extracts a PDF's existing text layer; declines scanned documents."""

    def __init__(self, settings: IngestionSettings | None = None) -> None:
        self._settings = settings or IngestionSettings()

    def supports(self, *, content_type: str, filename: str) -> bool:
        return (
            content_type.split(";")[0].strip().lower() in _PDF_TYPES
            or filename.lower().endswith(".pdf")
        )

    async def parse(self, *, data: bytes, content_type: str, filename: str) -> list[ParsedBlock]:
        del content_type
        if not self._settings.pdf_text_layer_first:
            raise ParserDeclined("text-layer fast path is disabled by configuration")
        # Off the event loop: PDFium is C, and a 500-page document is still
        # tens of milliseconds of blocking work that would otherwise stall
        # every other coroutine in the worker.
        return await asyncio.to_thread(self._parse_sync, data, filename)

    def _parse_sync(self, data: bytes, filename: str) -> list[ParsedBlock]:
        import pypdfium2

        try:
            document = pypdfium2.PdfDocument(data)
        except Exception as exc:
            # A password-protected or corrupt PDF. Not a decline: docling
            # cannot read it either, so let the tenant see a real failure.
            raise DocumentParseError(f"{filename}: could not be opened ({exc})") from exc

        try:
            pages = self._extract_pages(document)
        finally:
            document.close()

        if not pages:
            raise ParserDeclined(f"{filename}: no pages")

        populated = sum(1 for text in pages if len(text) >= self._settings.pdf_min_chars_per_page)
        coverage = populated / len(pages)
        if coverage < self._settings.pdf_min_text_page_ratio:
            # Scanned, or a deck of full-page images. Docling's OCR is the
            # only thing that will read it.
            logger.info(
                "pdf text layer too sparse (%d/%d pages); deferring to docling: %s",
                populated,
                len(pages),
                filename,
            )
            raise ParserDeclined(f"{filename}: no usable text layer")

        blocks: list[ParsedBlock] = []
        for number, text in enumerate(pages, start=1):
            blocks.extend(_page_to_blocks(text, page_number=number))
        if not blocks:
            raise ParserDeclined(f"{filename}: text layer produced no content")

        logger.info(
            "pdf parsed via text layer: %s (%d pages, %d blocks)", filename, len(pages), len(blocks)
        )
        return blocks

    def _extract_pages(self, document: Any) -> list[str]:
        pages: list[str] = []
        for page in document:
            textpage = None
            try:
                textpage = page.get_textpage()
                pages.append(textpage.get_text_bounded() or "")
            except Exception:
                # One unreadable page does not condemn the document; it just
                # counts as empty toward the coverage ratio below.
                pages.append("")
            finally:
                if textpage is not None:
                    textpage.close()
                page.close()
        return pages


def _page_to_blocks(text: str, *, page_number: int) -> list[ParsedBlock]:
    """Splits a page into paragraph blocks, all citing that page.

    Blank-line separation rather than anything cleverer: the text layer has no
    structure beyond position, and inventing headings from font sizes is
    exactly the guessing this parser exists to avoid. Chunking merges
    neighbours anyway -- what matters is that it never merges across pages,
    which distinct `source_location` values guarantee.
    """
    location = f"page {page_number}"
    blocks: list[ParsedBlock] = []
    for paragraph in text.split("\n\n"):
        cleaned = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
        if cleaned:
            blocks.append(ParsedBlock(text=cleaned, source_location=location))
    return blocks