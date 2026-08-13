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
from io import BytesIO
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


class _OutOfMemoryDuringConversion(Exception):
    """Internal signal that the first profile ran out of memory.

    Never escapes this module: it is caught by `_convert_sync`, which retries
    with the low-memory profile. If that also fails, the underlying error
    surfaces as an ordinary `DocumentParseError` and the tenant is told the
    document could not be read -- which by then is true.
    """


#: Substrings that mean "ran out of memory" across the C++, ONNX and Python
#: layers docling sits on. Matched case-insensitively against both exception
#: text and docling's own stage-error strings, because docling reports an
#: allocation failure as a status rather than by raising.
_MEMORY_MARKERS = (
    "bad_alloc",
    "out of memory",
    "cannot allocate",
    "unable to allocate",
    "memoryerror",
    "allocation failed",
)


def _looks_like_memory_error(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _MEMORY_MARKERS)


def _is_memory_failure(exc: BaseException) -> bool:
    return isinstance(exc, MemoryError) or _looks_like_memory_error(str(exc))


def _threaded_pipeline_cls() -> Any:
    """Docling's threaded PDF pipeline, when the installed release has one.

    Looked up rather than imported at module scope so a docling version
    without it degrades to the standard pipeline instead of failing to
    import. Pinning would be the alternative, and this package is already
    pinned tightly enough (`qdrant-client` taught that lesson); one more hard
    version coupling for an optional speedup is not worth it.
    """
    try:
        from docling.pipeline.threaded_standard_pdf_pipeline import (
            ThreadedStandardPdfPipeline,
        )

        return ThreadedStandardPdfPipeline
    except ImportError:
        return None


def _pipeline_options_cls() -> Any:
    """`ThreadedPdfPipelineOptions` when available, else the standard one.

    They must agree: the threaded pipeline rejects plain `PdfPipelineOptions`.
    Choosing both from the same probe keeps them in step.
    """
    if _threaded_pipeline_cls() is not None:
        try:
            from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions

            return ThreadedPdfPipelineOptions
        except ImportError:
            pass
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    return PdfPipelineOptions


def _pypdfium_backend() -> Any:
    """PDFium as the page backend, replacing docling's own C++ parser.

    The same library `fast_pdf.py` already uses for the text layer, so it is
    proven present, and it is the component whose allocation failures
    produced `std::bad_alloc` on a large scanned document.
    """
    try:
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

        return PyPdfiumDocumentBackend
    except ImportError:
        return None


def _rapid_ocr_options(settings: IngestionSettings) -> Any:
    """RapidOCR over onnxruntime, if this docling release exposes it.

    Returns `None` when it does not, leaving docling's default OCR in place --
    a heavier engine that still works. An OCR configuration that raises on
    import would take down every rich-format parse, which is a far worse
    outcome than using more memory.
    """
    try:
        from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions
    except ImportError:
        return None
    try:
        return RapidOcrOptions(
            backend="onnxruntime",
            # OCR only the regions layout analysis marked as text, instead of
            # every full page. On a text-native page that is close to no work
            # at all; on a scan it is the difference between OCRing a page and
            # OCRing its margins too.
            mode=OcrMode.PDF_AWARE_LAYOUT_REGIONS,
            lang=[settings.ocr_language],
            scale=settings.ocr_scale,
            print_verbose=False,
        )
    except Exception:
        # A signature change in a future release must not be fatal here.
        return None


class DoclingDocumentParser:
    def __init__(
        self,
        settings: IngestionSettings | None = None,
        *,
        converter: Any | None = None,
    ) -> None:
        # An injected converter is used verbatim -- that is the seam the
        # tests drive. The two lazily-built ones are the real path: a default
        # profile, and a one-page-at-a-time profile used only after the first
        # attempt runs out of memory.
        self._converter = converter
        self._default_converter: Any | None = None
        self._low_memory_converter: Any | None = None
        self._settings = settings or IngestionSettings()

    def supports(self, *, content_type: str, filename: str) -> bool:
        return (
            content_type.split(";")[0].strip().lower() in _SUPPORTED_TYPES
            or filename.lower().endswith(_SUPPORTED_EXTENSIONS)
        )

    def _get_converter(self, *, low_memory: bool = False) -> Any:
        if self._converter is not None:
            return self._converter
        _disable_torch_compilation()  # pragma: no cover - loads ML models
        if low_memory:
            if self._low_memory_converter is None:
                self._low_memory_converter = self._build_bounded_converter(low_memory=True)
            return self._low_memory_converter
        if self._default_converter is None:
            self._default_converter = self._build_bounded_converter(low_memory=False)
        return self._default_converter

    def _build_bounded_converter(self, *, low_memory: bool) -> Any:  # pragma: no cover - loads ML models
        """A converter with an explicit memory ceiling.

        Docling's defaults are tuned for a machine with room to spare: four
        pages through each stage at once, four threads, no document timeout.
        On a CPU worker parsing a scanned PDF -- where every page is rendered
        and pushed through OCR, layout and table models -- that combination
        exhausts the heap and raises `std::bad_alloc` part-way through, which
        docling reports per stage and then *continues past*, finishing
        "successfully" with nothing extracted.

        Three things keep that from happening, and the third is why a 40-page
        scanned deck that used to fail now has a second chance:

        1. **RapidOCR on onnxruntime**, not the default torch OCR stack. It is
           a fraction of the resident size, and `PDF_AWARE_LAYOUT_REGIONS`
           runs OCR only over regions the layout model says carry text rather
           than over every full page.
        2. **The PyPdfium backend**, which renders pages without docling's own
           C++ parser -- the component whose allocation failures were the
           `std::bad_alloc` in the first place.
        3. **A low-memory profile**, used on retry: every batch size drops to
           one page. Slower, and it either finishes or fails honestly.

        Bounding batches is the fix rather than lowering the OCR scale,
        because scale is what makes small print legible: reducing it would
        swap a visible crash for quietly worse text, which is the harder
        failure to notice.
        """
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )

        options = self._pdf_options(low_memory=low_memory)
        format_options: dict[Any, Any] = {}
        pipeline_cls = _threaded_pipeline_cls()
        backend = _pypdfium_backend()

        pdf_option_kwargs: dict[str, Any] = {"pipeline_options": options}
        if pipeline_cls is not None:
            pdf_option_kwargs["pipeline_cls"] = pipeline_cls
        if backend is not None:
            pdf_option_kwargs["backend"] = backend
        format_options[InputFormat.PDF] = PdfFormatOption(**pdf_option_kwargs)

        image_option_kwargs: dict[str, Any] = {"pipeline_options": options}
        if pipeline_cls is not None:
            image_option_kwargs["pipeline_cls"] = pipeline_cls
        format_options[InputFormat.IMAGE] = ImageFormatOption(**image_option_kwargs)

        del AcceleratorOptions  # imported for clarity above; used in _pdf_options
        return DocumentConverter(format_options=format_options)

    def _pdf_options(self, *, low_memory: bool) -> Any:  # pragma: no cover - loads ML models
        from docling.datamodel.accelerator_options import AcceleratorOptions

        batch = 1 if low_memory else max(1, self._settings.docling_batch_size)
        threads = 1 if low_memory else max(1, self._settings.docling_num_threads)

        options = _pipeline_options_cls()()
        options.layout_batch_size = batch
        options.ocr_batch_size = batch
        options.table_batch_size = batch
        if hasattr(options, "queue_max_size"):
            options.queue_max_size = 2 if low_memory else 4
        options.document_timeout = self._settings.docling_timeout_seconds
        options.accelerator_options = AcceleratorOptions(num_threads=threads)
        options.do_ocr = True
        options.do_table_structure = True
        # Page and picture images are generated for callers that want to
        # render them. Nothing here does, and each one is a full-resolution
        # bitmap held in memory per page.
        options.generate_page_images = False
        options.generate_picture_images = False

        ocr_options = _rapid_ocr_options(self._settings)
        if ocr_options is not None:
            options.ocr_options = ocr_options
        return options

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
        """Convert, and retry once at one page per batch if memory ran out.

        The retry exists because the first failure mode this parser met in
        production was not a broken file: it was a perfectly valid 40-page
        scanned deck whose pages, batched four at a time through OCR, layout
        and table models, exhausted the worker's heap. Retrying the *same*
        document with the *same* settings would fail identically, so the retry
        is only worth having because the second profile is genuinely
        different -- one page at a time, one thread.

        Only memory-shaped failures are retried. A corrupt or encrypted file
        fails the same way twice, and running it again would double the wait
        before the tenant is told what is wrong.
        """
        try:
            return self._convert_with(data, filename, low_memory=False)
        except _OutOfMemoryDuringConversion as exc:
            logger.warning(
                "docling ran out of memory on %s; retrying one page at a time: %s",
                filename,
                exc,
            )
            return self._convert_with(data, filename, low_memory=True)

    def _convert_with(self, data: bytes, filename: str, *, low_memory: bool) -> str:
        from docling.datamodel.base_models import ConversionStatus, DocumentStream

        stream = DocumentStream(name=filename, stream=BytesIO(data))
        try:
            result = self._get_converter(low_memory=low_memory).convert(
                stream,
                # Refuse an unreasonably long document up front rather than
                # discovering it page by page. Docling raises here, and the
                # caller turns that into a `failure_reason` the tenant can
                # act on.
                max_num_pages=self._settings.docling_max_pages,
            )
        except Exception as exc:
            if not low_memory and _is_memory_failure(exc):
                raise _OutOfMemoryDuringConversion(str(exc)) from exc
            raise

        # `PARTIAL_SUCCESS`, a populated `errors` list, and a document
        # containing only the pages that survived -- often none. Nothing here
        # used to look at either field, so "Stage preprocess failed for pages
        # [4..13]: std::bad_alloc" ended as a `ready` document with no chunks
        # and no error recorded anywhere.
        status = getattr(result, "status", None)
        first_error = _first_error(result)
        if status is ConversionStatus.FAILURE:
            # Docling reports an allocation failure as a *stage* error and a
            # FAILURE status rather than by raising, so the retry decision has
            # to be made from the message as well as from exceptions.
            if not low_memory and _looks_like_memory_error(first_error):
                raise _OutOfMemoryDuringConversion(first_error)
            raise DocumentParseError(f"{filename}: could not be parsed ({first_error})")
        if status is ConversionStatus.PARTIAL_SUCCESS:
            if not low_memory and _looks_like_memory_error(first_error):
                # Partial because pages were dropped for want of memory, not
                # because they were unreadable. Worth the slower second pass:
                # a document missing two thirds of its pages answers questions
                # wrongly rather than not at all.
                raise _OutOfMemoryDuringConversion(first_error)
            # Not raised: partial extraction is still worth indexing, and the
            # caller fails the document anyway if the surviving text chunks to
            # nothing. Logged so the cause is recoverable from the worker log
            # when a tenant asks why a document is thin.
            logger.warning(
                "docling partially converted %s -- some pages were lost: %s",
                filename,
                first_error,
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
