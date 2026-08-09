"""Cloudflare R2 ``ObjectStorageClient`` -- the production storage backend.

R2 speaks the S3 API, so this is plain ``boto3`` pointed at an R2 endpoint;
there is no R2-specific SDK and no R2-specific code path. Swapping to actual
AWS S3 (or MinIO, or any S3-compatible store) is an endpoint and credential
change, not a code change.

``boto3`` is imported lazily inside the constructor, matching
``infrastructure/secrets/aws_secrets_manager.py``: ``bootstrap.py`` imports
every adapter to build its selection map, so a module-scope import would make
a development environment that never sets ``STORAGE__MODE=r2`` fail to start
without boto3 installed.

**Why `asyncio.to_thread` rather than an async S3 library:** boto3 is
synchronous, and the secrets adapter already made this same call. Adding
``aioboto3`` for one adapter would mean a second AWS client stack, two
credential-resolution paths, and a dependency whose release cadence trails
boto3's. Object uploads are I/O-bound and infrequent relative to request
traffic, so a thread per call is the cheaper trade.

The client is built once and reused: botocore clients are thread-safe for this
usage, and constructing one per call would re-resolve credentials and
re-establish TLS on every upload.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from iam_platform.application.ai_resources.exceptions import DocumentContentNotFoundError

if TYPE_CHECKING:
    from iam_platform.core.config import StorageSettings


class CloudflareR2StorageClient:
    def __init__(self, settings: StorageSettings, *, client: Any | None = None) -> None:
        self._bucket = settings.r2_bucket
        if client is not None:
            # Injectable for tests -- exercises the real error-translation and
            # threading logic without reaching the network.
            self._client: Any = client
            return

        # pragma: no cover - requires R2 credentials to exercise
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError(
                "STORAGE__MODE=r2 requires boto3; install the 'aws' extra"
            ) from exc

        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
            # R2 ignores region, but S3 request signing requires one; "auto"
            # is what Cloudflare's own documentation specifies.
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    @staticmethod
    def _is_missing_key(exc: Exception) -> bool:
        """True when a botocore ``ClientError`` means "no such object".

        Duck-typed rather than caught by class so botocore stays a lazy
        import. Anything without a ``response`` mapping isn't a ClientError
        and is treated as a genuine fault.
        """
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        code = str(response.get("Error", {}).get("Code", ""))
        # S3 reports a missing key as NoSuchKey; a HEAD-shaped lookup can
        # surface the same condition as a bare 404 depending on the operation
        # and the caller's permissions.
        return code in {"NoSuchKey", "404"}

    async def put(self, *, path: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=path,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, *, path: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=path
            )
        except Exception as exc:
            # Only a genuinely-missing object becomes DocumentContentNotFound.
            # Denied, throttled, or bucket-missing must keep propagating:
            # flattening those into "not found" would report a
            # misconfiguration as absent data and send an operator looking in
            # entirely the wrong place.
            if self._is_missing_key(exc):
                raise DocumentContentNotFoundError(path) from exc
            raise
        body = await asyncio.to_thread(response["Body"].read)
        return bytes(body)

    async def delete(self, *, path: str) -> None:
        # S3's DeleteObject is already idempotent -- deleting an absent key
        # succeeds -- which matches the port's contract with no extra handling.
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=path)
