"""What a file actually *is*, decided from its bytes.

Every parser in this package used to be selected from the filename extension
and the ``Content-Type`` the client sent. Both are caller-controlled strings.
That is fine for choosing a *hint* and wrong for choosing a *parser*: a file
named ``report.pdf`` that is really a ZIP would be handed to the PDF path, and
a DOCX uploaded as ``application/octet-stream`` would be refused despite being
perfectly readable.

So detection happens here, from magic bytes and — for the ZIP-based formats,
which are indistinguishable at the first four bytes — the container's own
member list. The extension is consulted only as a last resort, for formats
that genuinely have no signature (CSV, plain text, XML).

**The archive guards are the security half.** OOXML, ODF and EPUB are ZIP
files, and a ZIP is an attacker-controlled decompression instruction. Without
a bound on entry count, total uncompressed size and per-entry compression
ratio, a few kilobytes of upload can ask a worker to materialise gigabytes.
The worker has database credentials and shares a host with other tenants'
jobs, so "the parse got slow" is the mild version of that failure.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    CSV = "csv"
    TSV = "tsv"
    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    JSONL = "jsonl"
    XML = "xml"
    EML = "eml"
    IMAGE = "image"
    EPUB = "epub"
    ODT = "odt"
    ODS = "ods"
    ODP = "odp"
    #: Pre-2007 binary Office. Detected by signature, parsed by docling.
    LEGACY_OFFICE = "legacy_office"
    RTF = "rtf"
    UNKNOWN = "unknown"


#: Formats whose container is a ZIP, and which therefore need the guards below
#: before anything opens them.
ZIP_CONTAINER_KINDS = frozenset(
    {
        FileKind.DOCX,
        FileKind.PPTX,
        FileKind.XLSX,
        FileKind.EPUB,
        FileKind.ODT,
        FileKind.ODS,
        FileKind.ODP,
    }
)


class UnsafeArchiveError(Exception):
    """A ZIP-based document that would cost more to open than it is worth.

    Deliberately not a `DocumentParseError`: the file is not malformed, it is
    hostile or accidentally enormous, and the distinction matters when reading
    the logs afterwards.
    """


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Bounds on what a ZIP-based document may expand to.

    Defaults are generous for real documents -- a 200-slide deck with photos
    sits far inside them -- and small enough that the pathological case is
    refused in milliseconds rather than swallowing the worker.
    """

    max_entries: int = 5_000
    max_uncompressed_bytes: int = 512 * 1024 * 1024
    #: Above this expansion factor an entry is only allowed if it is small.
    #: Ordinary compressed XML reaches 20-40x; 1000x is not a document.
    max_compression_ratio: float = 1000.0
    #: Entries under this size are exempt from the ratio check -- a 10-byte
    #: file that expands to 10 KB is a 1000x ratio and completely harmless.
    ratio_exempt_bytes: int = 10 * 1024 * 1024


_EXTENSION_FALLBACK: dict[str, FileKind] = {
    ".csv": FileKind.CSV,
    ".tsv": FileKind.TSV,
    ".txt": FileKind.TEXT,
    ".text": FileKind.TEXT,
    ".log": FileKind.TEXT,
    ".md": FileKind.MARKDOWN,
    ".markdown": FileKind.MARKDOWN,
    ".json": FileKind.JSON,
    ".jsonl": FileKind.JSONL,
    ".ndjson": FileKind.JSONL,
    ".xml": FileKind.XML,
    ".html": FileKind.HTML,
    ".htm": FileKind.HTML,
    ".eml": FileKind.EML,
}

#: OOXML/ODF member paths that identify the flavour inside an otherwise
#: identical ZIP.
_ZIP_MARKERS: tuple[tuple[str, FileKind], ...] = (
    ("word/document.xml", FileKind.DOCX),
    ("ppt/presentation.xml", FileKind.PPTX),
    ("xl/workbook.xml", FileKind.XLSX),
    ("META-INF/container.xml", FileKind.EPUB),
)

_ODF_MIMETYPES: tuple[tuple[str, FileKind], ...] = (
    ("opendocument.text", FileKind.ODT),
    ("opendocument.spreadsheet", FileKind.ODS),
    ("opendocument.presentation", FileKind.ODP),
)


def _extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def detect_kind(data: bytes, filename: str) -> FileKind:
    """Identify `data`, using `filename` only where bytes cannot decide.

    Signature checks run before the extension fallback, so a mislabelled file
    is routed by what it contains. That is a correctness win as often as a
    security one: browsers routinely send `application/octet-stream` for
    perfectly ordinary uploads.
    """
    head = data[:16]

    if head.startswith(b"%PDF-"):
        return FileKind.PDF
    if head.startswith(b"{\\rtf"):
        return FileKind.RTF
    # OLE2 compound file: .doc/.xls/.ppt, and also .msg. Docling handles the
    # Office ones; anything else in this container fails there with its own
    # message rather than being silently mis-parsed here.
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return FileKind.LEGACY_OFFICE
    if _is_image_signature(head):
        return FileKind.IMAGE

    if head.startswith(b"PK\x03\x04"):
        kind = _classify_zip(data)
        if kind is not FileKind.UNKNOWN:
            return kind

    return _EXTENSION_FALLBACK.get(_extension_of(filename), FileKind.UNKNOWN)


def _is_image_signature(head: bytes) -> bool:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head[:3] == b"\xff\xd8\xff":  # JPEG
        return True
    if head.startswith((b"GIF87a", b"GIF89a")):
        return True
    if head.startswith((b"II*\x00", b"MM\x00*")):  # TIFF, both endiannesses
        return True
    if head.startswith(b"BM"):
        return True
    # WEBP is a RIFF container; the format tag sits at offset 8.
    return head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP"


def _classify_zip(data: bytes) -> FileKind:
    """Which ZIP-based document this is, from the member list.

    Reads the central directory only -- no entry is decompressed here, so a
    hostile archive cannot spend anything during *identification*. Expansion
    is bounded separately by `assert_archive_is_safe`, which the caller runs
    before handing the bytes to a parser.
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            for marker, kind in _ZIP_MARKERS:
                if marker in names:
                    return kind
            if "mimetype" in names:
                mimetype = archive.read("mimetype")[:128].decode("ascii", "ignore")
                for needle, kind in _ODF_MIMETYPES:
                    if needle in mimetype:
                        return kind
    except (zipfile.BadZipFile, KeyError, OSError):
        return FileKind.UNKNOWN
    return FileKind.UNKNOWN


def assert_archive_is_safe(data: bytes, *, limits: ArchiveLimits | None = None) -> None:
    """Refuse a ZIP-based document that would expand unreasonably.

    Checked against the central directory, before any entry is read, so the
    refusal costs nothing. The declared sizes could of course be lies -- a
    truthful-looking header with a much larger payload is possible -- but the
    parsers downstream read through `zipfile`, which stops at the declared
    size, so a lie in this direction produces a truncated read rather than an
    unbounded one.
    """
    limits = limits or ArchiveLimits()
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsafeArchiveError(f"not a readable archive: {exc}") from exc

    if len(infos) > limits.max_entries:
        raise UnsafeArchiveError(
            f"archive contains {len(infos)} entries, above the limit of {limits.max_entries}"
        )

    total = 0
    for info in infos:
        total += info.file_size
        if total > limits.max_uncompressed_bytes:
            raise UnsafeArchiveError(
                "archive expands beyond the configured uncompressed-size limit "
                f"({limits.max_uncompressed_bytes} bytes)"
            )
        if (
            info.compress_size > 0
            and info.file_size > limits.ratio_exempt_bytes
            and info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            raise UnsafeArchiveError(
                f"entry {info.filename!r} expands "
                f"{info.file_size / info.compress_size:.0f}x, which is not a document"
            )
