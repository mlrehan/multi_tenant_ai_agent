"""Token-aware chunking -- 700 tokens with 100 overlap by default.

Splits on *token* count, not characters, because tokens are the unit the
embedding model actually bounds. A character-based split sized for English
would silently produce over-long chunks for text that tokenizes densely (CJK,
code, long URLs) and get truncated by the embeddings API -- losing the tail of
a passage with no error anywhere.

**Blocks are never merged across different ``source_location`` values.** That
is what keeps a citation honest: a chunk attributed to "page 7" must not
contain a sentence from page 8. Consecutive blocks sharing a location *are*
merged, so a page split into many small paragraphs still yields chunks of
useful size rather than a chunk per paragraph.

**The overlap exists to protect answers that straddle a boundary.** Without
it, a sentence split across two chunks appears in neither with enough context
to be retrievable; 100 tokens is enough to carry a complete thought across the
seam without materially inflating storage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from iam_platform.application.ai_resources.ports import ParsedBlock, TextChunk

if TYPE_CHECKING:
    from iam_platform.core.config import IngestionSettings


class TokenAwareChunker:
    def __init__(self, settings: IngestionSettings, *, encoder: Any | None = None) -> None:
        self._chunk_tokens = settings.chunk_tokens
        self._overlap_tokens = settings.chunk_overlap_tokens
        self._encoding_name = settings.tokenizer_encoding
        self._encoder = encoder

    def _get_encoder(self) -> Any:
        if self._encoder is None:
            import tiktoken

            self._encoder = tiktoken.get_encoding(self._encoding_name)
        return self._encoder

    def count_tokens(self, text: str) -> int:
        return len(self._get_encoder().encode(text))

    def chunk(self, blocks: list[ParsedBlock]) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for location, text in _group_by_location(blocks):
            chunks.extend(self._split_one(text, location))
        return chunks

    def _split_one(self, text: str, location: str | None) -> list[TextChunk]:
        encoder = self._get_encoder()
        tokens = encoder.encode(text)

        if len(tokens) <= self._chunk_tokens:
            stripped = text.strip()
            return (
                [TextChunk(text=stripped, token_count=len(tokens), source_location=location)]
                if stripped
                else []
            )

        # Slide a window of `chunk_tokens` forward by `chunk_tokens - overlap`.
        # Decoding each window back to text (rather than splitting the string
        # by character estimate) is what guarantees the count is exact.
        stride = self._chunk_tokens - self._overlap_tokens
        if stride <= 0:
            raise ValueError(
                f"chunk_overlap_tokens ({self._overlap_tokens}) must be smaller than "
                f"chunk_tokens ({self._chunk_tokens}) -- otherwise the window never advances"
            )

        chunks: list[TextChunk] = []
        for start in range(0, len(tokens), stride):
            window = tokens[start : start + self._chunk_tokens]
            if not window:
                break
            decoded = str(encoder.decode(window)).strip()
            if decoded:
                chunks.append(
                    TextChunk(
                        text=decoded, token_count=len(window), source_location=location
                    )
                )
            # A final window shorter than the stride means we've consumed the
            # input; without this, the last partial window repeats forever at
            # small overlaps.
            if start + self._chunk_tokens >= len(tokens):
                break
        return chunks


def _group_by_location(blocks: list[ParsedBlock]) -> list[tuple[str | None, str]]:
    """Merges *consecutive* blocks sharing a source location.

    Consecutive, not all-with-the-same-location: two separate passages that
    happen to both come from "Introduction" in different parts of a document
    are different passages, and joining them would fabricate adjacency that
    the source never had.
    """
    grouped: list[tuple[str | None, list[str]]] = []
    for block in blocks:
        if not block.text.strip():
            continue
        if grouped and grouped[-1][0] == block.source_location:
            grouped[-1][1].append(block.text)
        else:
            grouped.append((block.source_location, [block.text]))
    return [(location, "\n\n".join(parts)) for location, parts in grouped]
