"""Docling parser -- PDF, DOCX, XLSX, PPTX, and images.

One library for all five because they share the actual problem: recovering
*reading order* and structure from a format that encodes visual layout. A PDF
is a bag of positioned glyphs, not a paragraph stream; naive extraction
interleaves columns, drops table structure, and inlines headers mid-sentence.
Docling reconstructs the layout (and OCRs images), which is the difference
between chunks that read like prose and chunks that read like noise.

**Docling is imported lazily**, inside the constructor, for the same reason as
boto3 elsewhere: it pulls in large ML models and is only needed by worker
processes actually parsing rich documents. A module-scope import would make
the API process -- which never parses anything -- pay that cost at startup.

**Parsing runs on a thread.** Docling is synchronous and CPU-bound for tens of
seconds on a large PDF. Left on the event loop it would block every other
coroutine in the worker; `asyncio.to_thread` keeps the process responsive.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from iam_platform.application.ai_resources.exceptions import DocumentParseError
from iam_platform.application.ai_resources.ports import ParsedBlock
from iam_platform.core.config import IngestionSettings

logger = logging.getLogger("iam_platform.infrastructure.parsing.rich_documents")


def _first_error(result: Any) -> str:
    """A short, tenant-safe summary of why docling struggled.

    Only the first error: a 40-page document that ran out of memory produces
    one per page, and a `failure_reason` column is not the place for forty
    copies of the same sentence.
    """
    errors = getattr(result, "errors", None) or []
    if not errors:
        return "no further detail"
    first = errors[0]
    return str(getattr(first, "error_message", None) or first)[:300]

_SUPPORTED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
    "application/vnd.ms-powerpoint",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/webp",
}

_SUPPORTED_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".bmp",
    ".webp",
)


def _disable_torch_compilation() -> None:
    """Runs docling's models eagerly instead of through `torch.compile`.

    Docling's layout model goes through TorchInductor, which **generates C++
    and compiles it at runtime** -- so it needs a working C++ toolchain on the
    machine actually parsing the document, not on the machine that built it.

    That is a problem this project creates for itself. Phase 9's hardened
    runtime image deliberately ships no compiler (`Dockerfile`: build
    toolchain stays in the builder stage), which is right for a service
    handling tenant data -- and it means every PDF, DOCX, XLSX, PPTX and image
    would fail in production with `InvalidCxxCompiler`, while CSV/JSON/XML
    kept working. A partial, confusing failure rather than an obvious one.
    The same failure appears on a Windows dev machine without MSVC, which is
    how it was found: `Compiler: cl is not found`.

    Eager execution is slower per document but needs no compiler, and
    ingestion is already an asynchronous background job where a few extra
    seconds cost nothing. `setdefault` rather than a plain assignment so an
    operator who *has* a toolchain and wants the compiled path can opt back
    in by exporting the variable themselves.

    Set before docling is imported: `torch` binds config to the environment at
    import time, so doing this afterwards has no effect.

    **Both names are set deliberately.** `TORCHDYNAMO_DISABLE` is the name the
    older API used and is what most search results still suggest; on torch
    2.13 it is silently ignored -- setting it changes nothing and
    `torch._dynamo.config.disable` stays `False`. `TORCH_COMPILE_DISABLE` is
    the one that actually works. Setting only the obsolete name looks like a
    fix and is not, which is exactly how this was nearly missed: an early
    manual test appeared to pass with it and did not.
    """
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")


class DoclingDocumentParser:
    def __init__(
        self,
        settings: IngestionSettings | None = None,
        *,
        converter: Any | None = None,
    ) -> None:
        self._converter = converter
        self._settings = settings or IngestionSettings()

    def supports(self, *, content_type: str, filename: str) -> bool:
        return (
            content_type.split(";")[0].strip().lower() in _SUPPORTED_TYPES
            or filename.lower().endswith(_SUPPORTED_EXTENSIONS)
        )

    def _get_converter(self) -> Any:
        if self._converter is None:  # pragma: no cover - loads ML models
            _disable_torch_compilation()
            self._converter = self._build_bounded_converter()
        return self._converter

    def _build_bounded_converter(self) -> Any:  # pragma: no cover - loads ML models
        """A converter with an explicit memory ceiling.

        Docling's defaults are tuned for a machine with room to spare: four
        pages through each stage at once, four threads, no document timeout.
        On a CPU worker parsing a scanned PDF -- where every page is rendered
        at 3x scale and pushed through OCR, layout and table models -- that
        combination exhausts the heap and raises `std::bad_alloc` part-way
        through, which docling reports per stage and then *continues past*,
        finishing "successfully" with nothing extracted.

        Bounding the batch size is the fix rather than lowering `ocr scale`,
        because scale is what makes small print legible: reducing it would
        swap a visible crash for quietly worse text, which is the harder
        failure to notice. Slower and complete beats faster and empty.
        """
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        batch = max(1, self._settings.docling_batch_size)
        options = PdfPipelineOptions()
        options.layout_batch_size = batch
        options.ocr_batch_size = batch
        options.table_batch_size = batch
        options.document_timeout = self._settings.docling_timeout_seconds
        options.accelerator_options = AcceleratorOptions(
            num_threads=max(1, self._settings.docling_num_threads)
        )
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    async def parse(self, *, data: bytes, content_type: str, filename: str) -> list[ParsedBlock]:
        try:
            markdown = await asyncio.to_thread(self._convert_sync, data, filename)
        except DocumentParseError:
            raise
        except Exception as exc:
            # Docling raises a wide variety of format-specific errors (an
            # encrypted PDF, a truncated DOCX zip, an unreadable image). All
            # of them mean the same thing to the tenant -- "we could not read
            # your file" -- and all of them belong on `failure_reason` rather
            # than crashing the worker.
            raise DocumentParseError(f"{filename}: could not be parsed ({exc})") from exc

        return _markdown_to_blocks(markdown)

    def _convert_sync(self, data: bytes, filename: str) -> str:
        from docling.datamodel.base_models import ConversionStatus, DocumentStream

        stream = DocumentStream(name=filename, stream=__import__("io").BytesIO(data))
        result = self._get_converter().convert(
            stream,
            # Refuse an unreasonably long document up front rather than
            # discovering it page by page. Docling raises here, and the caller
            # turns that into a `failure_reason` the tenant can act on.
            max_num_pages=self._settings.docling_max_pages,
        )

        # **Docling reports per-stage failures and keeps going.** A scanned PDF
        # whose pages ran out of memory comes back with status
        # `PARTIAL_SUCCESS`, a populated `errors` list, and a document
        # containing only the pages that survived -- often none. Nothing here
        # used to look at either field, so "Stage preprocess failed for pages
        # [4..13]: std::bad_alloc" ended as a `ready` document with no chunks
        # and no error recorded anywhere.
        status = getattr(result, "status", None)
        if status is ConversionStatus.FAILURE:
            raise DocumentParseError(
                f"{filename}: could not be parsed ({_first_error(result)})"
            )
        if status is ConversionStatus.PARTIAL_SUCCESS:
            # Not raised: partial extraction is still worth indexing, and the
            # caller fails the document anyway if the surviving text chunks to
            # nothing. Logged so the cause is recoverable from the worker log
            # when a tenant asks why a document is thin.
            logger.warning(
                "docling partially converted %s -- some pages were lost: %s",
                filename,
                _first_error(result),
            )

        return str(result.document.export_to_markdown())


def _markdown_to_blocks(markdown: str) -> list[ParsedBlock]:
    """Splits docling's markdown into blocks, tracking the current heading.

    The heading is carried as ``source_location`` so a citation can name the
    section a passage came from. Docling emits page numbers inconsistently
    across formats (a spreadsheet has no pages), whereas headings exist in all
    of them -- so headings are the portable choice rather than page numbers.
    """
    blocks: list[ParsedBlock] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                blocks.append(ParsedBlock(text=text, source_location=current_heading))
            buffer.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # A heading closes the previous block: text under a new heading is
            # a new topic, and merging across that boundary is what produces
            # chunks that answer the wrong question.
            flush()
            current_heading = stripped.lstrip("#").strip() or None
            continue
        if not stripped:
            flush()
            continue
        buffer.append(stripped)

    flush()
    return blocks
