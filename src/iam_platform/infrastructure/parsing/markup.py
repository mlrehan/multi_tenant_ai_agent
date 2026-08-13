"""HTML files and saved email, neither of which this platform could read.

Both are ordinary things for a tenant to have: an exported help-centre page,
a forwarded policy thread. Until now the upload endpoint refused them with a
415 because no parser claimed the type, while the *crawler* happily ingested
the same HTML from a URL — the same content accepted or refused depending on
how it arrived.

Encoding is detected rather than assumed. A file saved from Word or exported
from a legacy system is routinely cp1252 or utf-16, and decoding those as
UTF-8 either raises or produces mojibake that embeds into nonsense.
"""

from __future__ import annotations

import asyncio
import re
from email import policy
from email.parser import BytesParser
from typing import Any

from iam_platform.application.ai_resources.exceptions import DocumentParseError
from iam_platform.application.ai_resources.ports import ParsedBlock

#: Tags that carry content worth indexing. `nav`/`footer`/`aside` are omitted
#: deliberately -- the crawl pipeline learned the hard way that boilerplate
#: indexes just as readily as substance and then answers questions with a
#: cookie banner.
_CONTENT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "table")

_STRIP_TAGS = ("script", "style", "noscript", "template", "svg")


#: Charsets whose declared name means something other than what it says.
#: `latin-1` is routinely written by producers that are really emitting
#: cp1252, and cp1252 is a strict superset over the bytes that matter, so
#: honouring the declaration literally loses curly quotes and em dashes.
_CHARSET_ALIASES = {
    "latin-1": "cp1252",
    "latin1": "cp1252",
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "ascii": "utf-8",
    "us-ascii": "utf-8",
}

_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE
)


def declared_charset(data: bytes) -> str | None:
    """The encoding an HTML document claims, from its own `<meta>` tag.

    Only the head of the file is searched, because that is the only place the
    declaration is meaningful and because a `charset=` string can appear in
    ordinary prose further down.
    """
    match = _META_CHARSET.search(data[:4096])
    return match.group(1).decode("ascii", "ignore") if match else None


def decode_text(data: bytes, *, declared: str | None = None) -> str:
    """Text from bytes: what the file says first, statistics only as a last
    resort.

    The order matters and is not arbitrary.

    **UTF-8 wins over a declaration**, because a byte sequence that decodes
    cleanly as UTF-8 is essentially never anything else by accident, while a
    stale `charset=iso-8859-1` in a template that now emits UTF-8 is an
    everyday occurrence.

    **A declaration beats detection**, because it is a statement by whoever
    wrote the file rather than an inference from byte frequencies. Detection
    needs a few hundred bytes to mean anything -- on a short document it will
    confidently decode a French `caf\xe9` into an Arabic presentation form --
    and short documents (an exported email, an HTML fragment) are ordinary
    here.

    The final `errors="replace"` exists so one bad byte degrades one character
    instead of failing a document a person is waiting on.
    """
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    for candidate in (declared, declared_charset(data)):
        if not candidate:
            continue
        normalised = _CHARSET_ALIASES.get(candidate.strip().lower(), candidate.strip().lower())
        try:
            return data.decode(normalised)
        except (UnicodeDecodeError, LookupError):
            continue

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except ImportError:  # pragma: no cover - dependency is declared
        pass
    return data.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    return " ".join(text.split())


class HtmlParser:
    """HTML, via BeautifulSoup, keeping the heading trail as provenance."""

    async def parse(self, *, data: bytes, filename: str) -> list[ParsedBlock]:
        return await asyncio.to_thread(self._parse_sync, data, filename)

    def _parse_sync(self, data: bytes, filename: str) -> list[ParsedBlock]:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise DocumentParseError(f"{filename}: HTML support is unavailable") from exc

        try:
            soup = BeautifulSoup(decode_text(data), "lxml")
        except Exception as exc:
            raise DocumentParseError(f"{filename}: could not be parsed as HTML ({exc})") from exc

        for tag in soup(list(_STRIP_TAGS)):
            tag.decompose()

        blocks: list[ParsedBlock] = []
        headings: list[str] = []

        for element in soup.find_all(list(_CONTENT_TAGS)):
            if element.name == "table":
                table = _html_table(element)
                if table:
                    blocks.append(
                        ParsedBlock(text=table, source_location=_trail(headings, "table"))
                    )
                continue

            text = _clean(element.get_text(" ", strip=True))
            if not text:
                continue
            if element.name[0] == "h" and element.name[1:].isdigit():
                level = int(element.name[1:])
                headings = headings[: level - 1] + [text]
            blocks.append(ParsedBlock(text=text, source_location=_trail(headings, None)))

        return blocks


def _html_table(element: Any) -> str:
    from iam_platform.infrastructure.parsing.native_office import _markdown_table

    rows = [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        for row in element.find_all("tr")
    ]
    return _markdown_table([row for row in rows if row])


def _trail(headings: list[str], suffix: str | None) -> str | None:
    trail = " > ".join(h for h in headings if h)
    if suffix:
        return f"{trail} ({suffix})" if trail else suffix
    return trail or None


class EmlParser:
    """A saved `.eml` message: headers, body, and the names of attachments.

    The body is taken from `text/plain` when the message offers it and the
    HTML alternative only otherwise -- the plain part is what the sender
    actually wrote, while the HTML one carries a signature block, a tracking
    pixel and three nested quote levels of styling.

    **Attachments are named, not opened.** Parsing them here would mean one
    upload silently becoming many documents, with no row in `documents` for
    any of them and no way for a tenant to delete one. If attachment contents
    are wanted, they are their own upload.
    """

    async def parse(self, *, data: bytes, filename: str) -> list[ParsedBlock]:
        return await asyncio.to_thread(self._parse_sync, data, filename)

    def _parse_sync(self, data: bytes, filename: str) -> list[ParsedBlock]:
        try:
            message = BytesParser(policy=policy.default).parsebytes(data)
        except Exception as exc:
            raise DocumentParseError(f"{filename}: could not be read as email ({exc})") from exc

        blocks: list[ParsedBlock] = []

        headers = [
            (label, str(message.get(field, "")))
            for label, field in (
                ("Subject", "subject"),
                ("From", "from"),
                ("To", "to"),
                ("Cc", "cc"),
                ("Date", "date"),
            )
        ]
        summary = "\n".join(f"{label}: {value}" for label, value in headers if value)
        if summary:
            blocks.append(ParsedBlock(text=summary, source_location="headers"))

        plain: list[str] = []
        html_fallback: list[str] = []
        attachments: list[str] = []

        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                name = part.get_filename()
                if name:
                    attachments.append(name)
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain.append(_clean(_part_text(part)))
            elif content_type == "text/html":
                html_fallback.append(_clean(_html_to_text(_part_text(part))))

        body = [text for text in (plain or html_fallback) if text]
        blocks.extend(ParsedBlock(text=text, source_location="body") for text in body)

        if attachments:
            blocks.append(
                ParsedBlock(
                    text="Attachments: " + ", ".join(attachments),
                    source_location="attachments",
                )
            )

        return blocks


def _part_text(part: Any) -> str:
    try:
        content = part.get_content()
    except Exception:
        # A malformed or unknown charset makes `get_content` raise. The part's
        # own `charset=` is still the best hint available, so it is handed to
        # the decoder rather than thrown away with the exception.
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return ""
        return decode_text(payload, declared=part.get_content_charset())
    return content if isinstance(content, str) else ""


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:
        return html
