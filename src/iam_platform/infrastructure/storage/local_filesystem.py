"""Local-disk ``ObjectStorageClient`` -- the development storage backend.

Chosen for development because it needs no credentials and no network, so a
contributor can run the full ingestion pipeline offline. Production uses
``CloudflareR2StorageClient`` instead; the switch is an explicit
``STORAGE__MODE`` setting rather than an inference from whether credentials
happen to be configured (see ``core.config.StorageSettings``).

**Path containment is enforced here, not assumed.** Storage paths are
server-derived (``ObjectStoragePathFactory``), so a traversal sequence should
never reach this class -- but "should never" is not a control. A relative path
joined onto a root directory is the textbook directory-traversal sink, and the
cost of resolving both sides and comparing is a few microseconds, so this
verifies containment on every call rather than trusting its caller. That the
factory is already correct makes this defense-in-depth, not redundancy: it
holds even if a future caller passes a path from somewhere else.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from iam_platform.application.ai_resources.exceptions import DocumentContentNotFoundError


class LocalFilesystemStorageClient:
    def __init__(self, root: str) -> None:
        # `resolve()` collapses symlinks and `..` now, once, so every later
        # comparison is against a real canonical path.
        self._root = Path(root).resolve()

    def _resolve_within_root(self, path: str) -> Path:
        candidate = (self._root / path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(
                f"refusing to access {path!r}: resolves outside the configured storage root"
            )
        return candidate

    async def put(self, *, path: str, data: bytes, content_type: str) -> None:
        # `content_type` is deliberately unused: a filesystem has nowhere to
        # put it. It stays in the port signature because R2/S3 does store it,
        # and the database keeps `documents.content_type` regardless -- so
        # nothing is lost by this backend ignoring it.
        del content_type
        target = self._resolve_within_root(path)
        await asyncio.to_thread(self._write_sync, target, data)

    @staticmethod
    def _write_sync(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary sibling then rename: `os.replace` is atomic on
        # both POSIX and Windows, so a crash mid-write leaves either the old
        # bytes or the new ones, never a truncated file that a later parse
        # would treat as a corrupt document.
        temporary = target.with_name(f"{target.name}.partial")
        temporary.write_bytes(data)
        temporary.replace(target)

    async def get(self, *, path: str) -> bytes:
        target = self._resolve_within_root(path)
        try:
            return await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError as exc:
            raise DocumentContentNotFoundError(path) from exc

    async def delete(self, *, path: str) -> None:
        target = self._resolve_within_root(path)
        # `missing_ok=True` is the port's idempotency contract: a purge job
        # that crashed half-way must be safe to re-run.
        await asyncio.to_thread(lambda: target.unlink(missing_ok=True))
