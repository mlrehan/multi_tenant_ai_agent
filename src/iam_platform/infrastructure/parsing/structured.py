"""Parsers for structured formats -- CSV, JSON, XML.

Deliberately stdlib rather than docling. These formats have no layout to
reconstruct: the extraction is a tree or table walk, and running them through
a document-understanding pipeline would be slower, less predictable, and would
lose exactly the structure that makes a good ``source_location``
("Sheet row 42", ``/catalog/book[3]/title``).

**Every parser here bounds its own output.** A 10 MB JSON file of deeply
nested arrays can expand to far more text than its byte size suggests, and an
unbounded walk would hand the chunker something that costs real money to
embed. Limits are applied per-parser rather than centrally so each can be
expressed in the unit that makes sense for its format (rows, nodes, depth).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from xml.etree import ElementTree

from iam_platform.application.ai_resources.exceptions import DocumentParseError
from iam_platform.application.ai_resources.ports import ParsedBlock

#: Guards against a pathological file producing an unbounded block list. Well
#: past any realistic knowledge-base document; the point is to fail loudly
#: rather than quietly bill for a million embeddings.
_MAX_ROWS = 50_000
_MAX_JSON_NODES = 50_000
_MAX_XML_ELEMENTS = 50_000

_CSV_TYPES = {"text/csv", "application/csv", "text/tab-separated-values"}
_JSON_TYPES = {"application/json", "text/json"}
_XML_TYPES = {"application/xml", "text/xml"}


def _decode(data: bytes, *, filename: str) -> str:
    """UTF-8 with a BOM-tolerant fallback.

    Spreadsheet exports are routinely UTF-8-with-BOM or cp1252; refusing them
    would reject a large share of real-world CSV. Latin-1 is the last resort
    because it cannot fail -- better slightly wrong glyphs in a rare file than
    a whole document rejected.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError(f"{filename}: could not decode as text in any supported encoding")


class CsvParser:
    def supports(self, *, content_type: str, filename: str) -> bool:
        return content_type.split(";")[0].strip().lower() in _CSV_TYPES or filename.lower().endswith(
            (".csv", ".tsv")
        )

    async def parse(self, *, data: bytes, content_type: str, filename: str) -> list[ParsedBlock]:
        text = _decode(data, filename=filename)
        try:
            # Sniff the delimiter rather than assuming a comma -- TSV and
            # semicolon-separated exports are common enough that hardcoding
            # would mangle them into one giant column.
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.get_dialect("excel")  # type: ignore[assignment]

        reader = csv.reader(io.StringIO(text), dialect)
        try:
            rows = list(reader)
        except csv.Error as exc:
            raise DocumentParseError(f"{filename}: malformed CSV ({exc})") from exc

        if not rows:
            return []
        if len(rows) > _MAX_ROWS:
            raise DocumentParseError(
                f"{filename}: {len(rows)} rows exceeds the {_MAX_ROWS}-row ingestion limit"
            )

        header, *body = rows
        blocks: list[ParsedBlock] = []
        for index, row in enumerate(body, start=2):  # header is row 1
            # Rendered as "column: value" pairs rather than raw comma-joined
            # cells, because the embedding has no other way to know what a
            # value means -- "2019" is meaningless, "founded: 2019" is not.
            rendered = "; ".join(
                f"{name}: {value}"
                for name, value in zip(header, row, strict=False)
                if str(value).strip()
            )
            if rendered:
                blocks.append(ParsedBlock(text=rendered, source_location=f"row {index}"))
        return blocks


class JsonParser:
    def supports(self, *, content_type: str, filename: str) -> bool:
        return content_type.split(";")[0].strip().lower() in _JSON_TYPES or filename.lower().endswith(
            ".json"
        )

    async def parse(self, *, data: bytes, content_type: str, filename: str) -> list[ParsedBlock]:
        text = _decode(data, filename=filename)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DocumentParseError(f"{filename}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc

        blocks: list[ParsedBlock] = []
        self._walk(document, path="$", blocks=blocks, filename=filename)
        return blocks

    def _walk(self, node: Any, *, path: str, blocks: list[ParsedBlock], filename: str) -> None:
        if len(blocks) > _MAX_JSON_NODES:
            raise DocumentParseError(
                f"{filename}: exceeds the {_MAX_JSON_NODES}-node ingestion limit"
            )
        if isinstance(node, dict):
            # A dict of scalars is emitted as one block rather than one block
            # per key: "name: Ada; role: Engineer" retains the association
            # between fields that separate blocks would destroy.
            scalars = {k: v for k, v in node.items() if not isinstance(v, dict | list)}
            if scalars:
                rendered = "; ".join(f"{k}: {v}" for k, v in scalars.items() if str(v).strip())
                if rendered:
                    blocks.append(ParsedBlock(text=rendered, source_location=path))
            for key, value in node.items():
                if isinstance(value, dict | list):
                    self._walk(value, path=f"{path}.{key}", blocks=blocks, filename=filename)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                self._walk(item, path=f"{path}[{index}]", blocks=blocks, filename=filename)
        elif str(node).strip():
            blocks.append(ParsedBlock(text=str(node), source_location=path))


class XmlParser:
    def supports(self, *, content_type: str, filename: str) -> bool:
        return content_type.split(";")[0].strip().lower() in _XML_TYPES or filename.lower().endswith(
            ".xml"
        )

    async def parse(self, *, data: bytes, content_type: str, filename: str) -> list[ParsedBlock]:
        try:
            # `ElementTree` does not expand external entities or resolve
            # DOCTYPE references, so the classic XXE file-read and
            # billion-laughs vectors do not apply here. Using `lxml` (also a
            # project dependency) would require explicitly disabling both.
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError as exc:
            raise DocumentParseError(f"{filename}: invalid XML ({exc})") from exc

        blocks: list[ParsedBlock] = []
        for count, element in enumerate(root.iter()):
            if count > _MAX_XML_ELEMENTS:
                raise DocumentParseError(
                    f"{filename}: exceeds the {_MAX_XML_ELEMENTS}-element ingestion limit"
                )
            content = (element.text or "").strip()
            if not content:
                continue
            attributes = "; ".join(f"{k}={v}" for k, v in element.attrib.items())
            rendered = f"{element.tag}: {content}"
            if attributes:
                rendered = f"{rendered} ({attributes})"
            blocks.append(ParsedBlock(text=rendered, source_location=element.tag))
        return blocks
