"""The infrastructure half of BYOK: the only place a provider key is plaintext.

`test_answer_question`-level tests drive fakes and therefore prove the
*ciphertext* is forwarded, not that anything can decrypt it or that the right
client is used. That gap is where the model-configuration pass's real defect
lived (a repository `UPDATE` missing a column, invisible to every fake), so the
adapter gets its own tests.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    ProviderCredentialUnusableError,
)
from iam_platform.application.ai_resources.ports import (
    GroundingContext,
    RetrievedChunk,
)
from iam_platform.core.config import OpenAISettings
from iam_platform.infrastructure.chat.openai_chat import (
    OpenAIChatModel,
    _is_auth_rejection,
)
from iam_platform.infrastructure.security.encryption import FernetCredentialEncryptor

pytestmark = pytest.mark.unit

# Generated with `Fernet.generate_key()`; a test fixture, not a deployment key.
DATA_KEY = "aVfBdU7dvXbCEAAXjwbHHo1v6nCPzq7hZm5rNaHiQ4Y="
TENANT_KEY = "sk-tenant-key-abc123"


class _StubStream:
    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _RecordingClient:
    """Stands in for `AsyncOpenAI`, recording the key it was built with."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.closed = False
        self.chat = self  # type: ignore[assignment]
        self.completions = self  # type: ignore[assignment]

    async def create(self, **_kwargs: Any) -> Any:
        return _StubStream()

    async def close(self) -> None:
        self.closed = True


def _settings() -> OpenAISettings:
    return OpenAISettings(api_key="sk-platform-key")  # type: ignore[arg-type]


def _model(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> OpenAIChatModel:
    """Builds the adapter with `AsyncOpenAI` replaced, so no network is reachable
    and the key handed to the constructor is observable."""
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _RecordingClient)
    return OpenAIChatModel(
        _settings(),
        client=_RecordingClient("sk-platform-key"),
        credential_encryptor=FernetCredentialEncryptor(DATA_KEY),
        **kw,
    )


def _context() -> list[GroundingContext]:
    chunk = RetrievedChunk(
        chunk_id=uuid4(), document_id=uuid4(), text="Refunds take 5 days.", score=0.9
    )
    return [GroundingContext(label="1", text=chunk.text, chunk=chunk)]


async def _drain(model: OpenAIChatModel, ciphertext: bytes | None) -> None:
    async for _piece in model.stream_answer(
        question="Refunds?",
        context=_context(),
        system_prompt="sys",
        credential_ciphertext=ciphertext,
    ):
        pass


class TestByokChatAdapter:
    async def test_the_tenants_key_is_decrypted_and_used_for_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The claim the application-layer tests structurally cannot make: the
        ciphertext becomes the *actual* key on the request."""
        model = _model(monkeypatch)
        ciphertext = FernetCredentialEncryptor(DATA_KEY).encrypt(TENANT_KEY)

        await _drain(model, ciphertext)

        clients = list(model._byok_clients.values())
        assert [c.api_key for c in clients] == [TENANT_KEY]

    async def test_no_ciphertext_leaves_the_platform_client_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = _model(monkeypatch)
        await _drain(model, None)
        assert model._byok_clients == {}

    async def test_the_same_credential_reuses_one_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Building a client per request means a new connection pool per
        request: no keep-alive, and sockets accumulating faster than they are
        reclaimed. Asserted on identity, since a second client with the same
        key would look correct in every other respect."""
        model = _model(monkeypatch)
        ciphertext = FernetCredentialEncryptor(DATA_KEY).encrypt(TENANT_KEY)

        await _drain(model, ciphertext)
        first = next(iter(model._byok_clients.values()))
        await _drain(model, ciphertext)

        assert len(model._byok_clients) == 1
        assert next(iter(model._byok_clients.values())) is first

    async def test_a_rotated_credential_does_not_reuse_the_old_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rotation must take effect on the next call. Caching on the credential
        *id* would have served the superseded key indefinitely; the cache is
        keyed by a digest of the ciphertext precisely so it cannot."""
        model = _model(monkeypatch)
        encryptor = FernetCredentialEncryptor(DATA_KEY)

        await _drain(model, encryptor.encrypt(TENANT_KEY))
        await _drain(model, encryptor.encrypt("sk-rotated-key-xyz789"))

        assert sorted(c.api_key for c in model._byok_clients.values()) == [
            "sk-rotated-key-xyz789",
            TENANT_KEY,
        ]

    async def test_undecryptable_bytes_refuse_and_do_not_leak(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ciphertext this deployment's data key cannot open -- a restore from a
        backup taken under a different key. Refused, and the message says what
        to do rather than reproducing any part of the stored value."""
        model = _model(monkeypatch)

        with pytest.raises(ProviderCredentialUnusableError) as caught:
            await _drain(model, b"not-a-fernet-token")

        assert "re-entered" in str(caught.value)
        assert model._byok_clients == {}

    async def test_a_deployment_with_no_encryptor_refuses_rather_than_using_the_platform_key(
        self,
    ) -> None:
        """An adapter built without an encryptor cannot honour BYOK. Refusing is
        the same call as everywhere else in this feature: answering on the
        platform's key would move the bill silently."""
        model = OpenAIChatModel(
            _settings(), client=_RecordingClient("sk-platform-key")
        )
        with pytest.raises(ProviderCredentialUnusableError):
            await _drain(model, b"anything")

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [(401, True), (403, True), (429, False), (500, False), (None, False)],
    )
    def test_only_key_rejections_are_reported_as_a_credential_problem(
        self, status_code: int | None, expected: bool
    ) -> None:
        """A 429 or a 500 is not a bad key, and telling a tenant admin to
        re-enter a working credential over a rate limit sends them to fix
        something that is not broken."""
        exc = RuntimeError("upstream said no")
        if status_code is not None:
            exc.status_code = status_code  # type: ignore[attr-defined]
        assert _is_auth_rejection(exc) is expected

    async def test_a_rejected_tenant_key_is_reported_as_a_credential_problem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = _model(monkeypatch)
        ciphertext = FernetCredentialEncryptor(DATA_KEY).encrypt(TENANT_KEY)

        async def _refuse(_self: Any, **_kwargs: Any) -> Any:
            err = RuntimeError("Incorrect API key provided")
            err.status_code = 401  # type: ignore[attr-defined]
            raise err

        monkeypatch.setattr(_RecordingClient, "create", _refuse)

        with pytest.raises(ProviderCredentialUnusableError) as caught:
            await _drain(model, ciphertext)
        assert "rejected" in str(caught.value)

    async def test_the_platform_keys_own_401_is_not_blamed_on_the_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same 401 without a tenant credential is a deployment fault. It
        must propagate as itself: `ProviderCredentialUnusableError` renders as
        "fix this configuration's credential", and there is no credential on the
        configuration to fix."""
        model = _model(monkeypatch)

        async def _refuse(_self: Any, **_kwargs: Any) -> Any:
            err = RuntimeError("platform key is bad")
            err.status_code = 401  # type: ignore[attr-defined]
            raise err

        monkeypatch.setattr(_RecordingClient, "create", _refuse)

        with pytest.raises(RuntimeError) as caught:
            await _drain(model, None)
        assert not isinstance(caught.value, ProviderCredentialUnusableError)
