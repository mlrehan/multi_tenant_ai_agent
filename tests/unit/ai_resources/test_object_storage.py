"""Object-storage adapters: the local filesystem backend against a real temp
directory, and the R2 backend against an injected fake S3 client.

The local backend is tested against real files rather than a mock because the
properties worth proving here -- path containment, atomic replace, idempotent
delete -- are all properties of actual filesystem behaviour. A mock would
assert that the code calls the functions it calls, which is not the same
thing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iam_platform.application.ai_resources.exceptions import DocumentContentNotFoundError
from iam_platform.core.config import StorageSettings
from iam_platform.infrastructure.storage.cloudflare_r2 import CloudflareR2StorageClient
from iam_platform.infrastructure.storage.local_filesystem import LocalFilesystemStorageClient

pytestmark = pytest.mark.unit


class TestLocalFilesystemStorageClient:
    async def test_put_then_get_round_trips(self, tmp_path: Path) -> None:
        client = LocalFilesystemStorageClient(str(tmp_path))
        await client.put(path="tenant/kb/doc", data=b"hello", content_type="text/plain")

        assert await client.get(path="tenant/kb/doc") == b"hello"

    async def test_put_creates_nested_directories(self, tmp_path: Path) -> None:
        """Storage paths are `{tenant}/{kb}/{document}` -- three levels deep,
        none of which exist before the first upload for that knowledge base."""
        client = LocalFilesystemStorageClient(str(tmp_path))
        await client.put(path="a/b/c/d", data=b"x", content_type="text/plain")

        assert (tmp_path / "a" / "b" / "c" / "d").read_bytes() == b"x"

    async def test_put_overwrites_existing_content(self, tmp_path: Path) -> None:
        client = LocalFilesystemStorageClient(str(tmp_path))
        await client.put(path="doc", data=b"first", content_type="text/plain")
        await client.put(path="doc", data=b"second", content_type="text/plain")

        assert await client.get(path="doc") == b"second"

    async def test_put_leaves_no_partial_file_behind(self, tmp_path: Path) -> None:
        """The atomic write goes via a `.partial` sibling; it must not survive."""
        client = LocalFilesystemStorageClient(str(tmp_path))
        await client.put(path="doc", data=b"x", content_type="text/plain")

        assert [p.name for p in tmp_path.iterdir()] == ["doc"]

    async def test_get_missing_object_raises_document_content_not_found(
        self, tmp_path: Path
    ) -> None:
        client = LocalFilesystemStorageClient(str(tmp_path))

        with pytest.raises(DocumentContentNotFoundError):
            await client.get(path="never/written")

    async def test_delete_is_idempotent(self, tmp_path: Path) -> None:
        """A purge job that crashes half-way must be safe to re-run."""
        client = LocalFilesystemStorageClient(str(tmp_path))
        await client.put(path="doc", data=b"x", content_type="text/plain")

        await client.delete(path="doc")
        await client.delete(path="doc")  # must not raise

        with pytest.raises(DocumentContentNotFoundError):
            await client.get(path="doc")

    @pytest.mark.parametrize(
        "hostile_path",
        [
            "../escaped",
            "tenant/../../escaped",
            "a/b/../../../escaped",
        ],
    )
    async def test_traversal_outside_the_root_is_refused(
        self, tmp_path: Path, hostile_path: str
    ) -> None:
        """Defense in depth: storage paths are server-derived, so a traversal
        sequence should never arrive -- but joining a relative path onto a root
        is the textbook traversal sink, so containment is verified rather than
        assumed. This holds even if a future caller passes a path from
        somewhere other than ObjectStoragePathFactory.
        """
        root = tmp_path / "root"
        root.mkdir()
        client = LocalFilesystemStorageClient(str(root))

        with pytest.raises(ValueError, match="outside the configured storage root"):
            await client.put(path=hostile_path, data=b"pwned", content_type="text/plain")

        # And nothing was written anywhere outside the root.
        assert not (tmp_path / "escaped").exists()

    async def test_traversal_is_refused_on_read_and_delete_too(self, tmp_path: Path) -> None:
        """A guard on `put` alone would still leak arbitrary file *reads*."""
        root = tmp_path / "root"
        root.mkdir()
        (tmp_path / "secret").write_bytes(b"other tenant's data")
        client = LocalFilesystemStorageClient(str(root))

        with pytest.raises(ValueError, match="outside the configured storage root"):
            await client.get(path="../secret")
        with pytest.raises(ValueError, match="outside the configured storage root"):
            await client.delete(path="../secret")

        assert (tmp_path / "secret").exists()


class _FakeS3Client:
    """Minimal stand-in for a botocore S3 client.

    Deliberately mimics botocore's *error shape* (a `ClientError`-alike with a
    `response` mapping) rather than raising a custom exception, because the
    adapter's job is precisely to translate that shape -- a fake that raised
    something simpler would let a mistranslation pass.
    """

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        del Bucket
        self.objects[Key] = (Body, ContentType)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        del Bucket
        if Key not in self.objects:
            raise _FakeClientError({"Error": {"Code": "NoSuchKey"}})
        data, _ = self.objects[Key]
        return {"Body": _FakeBody(data)}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        del Bucket
        self.objects.pop(Key, None)


class _FakeClientError(Exception):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(str(response))
        self.response = response


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _r2_settings() -> StorageSettings:
    return StorageSettings(
        mode="r2",
        r2_account_id="acct",
        r2_bucket="bucket",
        r2_access_key_id="key",  # type: ignore[arg-type]
        r2_secret_access_key="secret",  # type: ignore[arg-type]
    )


class TestCloudflareR2StorageClient:
    async def test_put_then_get_round_trips(self) -> None:
        fake = _FakeS3Client()
        client = CloudflareR2StorageClient(_r2_settings(), client=fake)

        await client.put(path="tenant/kb/doc", data=b"hello", content_type="application/pdf")

        assert await client.get(path="tenant/kb/doc") == b"hello"

    async def test_put_forwards_content_type(self) -> None:
        """Unlike the filesystem backend, S3 stores content type -- so it must
        actually be passed through, not dropped."""
        fake = _FakeS3Client()
        client = CloudflareR2StorageClient(_r2_settings(), client=fake)

        await client.put(path="doc", data=b"x", content_type="application/pdf")

        assert fake.objects["doc"][1] == "application/pdf"

    async def test_missing_key_becomes_document_content_not_found(self) -> None:
        client = CloudflareR2StorageClient(_r2_settings(), client=_FakeS3Client())

        with pytest.raises(DocumentContentNotFoundError):
            await client.get(path="never/written")

    async def test_non_missing_client_errors_are_not_swallowed(self) -> None:
        """AccessDenied must NOT be reported as "not found".

        Flattening every ClientError into DocumentContentNotFoundError would
        turn a credentials/permissions misconfiguration into "your document
        doesn't exist" -- sending whoever debugs it to look in entirely the
        wrong place.
        """

        class _DenyingClient(_FakeS3Client):
            def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
                raise _FakeClientError({"Error": {"Code": "AccessDenied"}})

        client = CloudflareR2StorageClient(_r2_settings(), client=_DenyingClient())

        with pytest.raises(_FakeClientError):
            await client.get(path="doc")

    async def test_delete_is_idempotent(self) -> None:
        client = CloudflareR2StorageClient(_r2_settings(), client=_FakeS3Client())
        await client.delete(path="never/written")  # must not raise


class TestStorageSettingsValidation:
    def test_r2_mode_without_credentials_is_refused_at_construction(self) -> None:
        """Falling back to local disk in production would mean uploads landing
        on an ephemeral container filesystem while reporting success."""
        with pytest.raises(ValueError, match="STORAGE__MODE=r2 requires"):
            StorageSettings(mode="r2")

    def test_local_mode_needs_no_r2_credentials(self) -> None:
        assert StorageSettings(mode="local").local_path == "var/storage"
