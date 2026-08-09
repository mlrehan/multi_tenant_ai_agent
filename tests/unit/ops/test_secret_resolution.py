"""Secret resolution -- the Phase 9 gap where ``secret://`` references were
designed (docs/21) but never actually resolved, so a production deploy would
have used the literal string ``secret://prod/db/password`` as a password.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from iam_platform.core.config import Settings
from iam_platform.core.errors import SecretNotFoundError
from iam_platform.infrastructure.secrets.resolver import is_secret_reference, resolve_secrets


class FakeSecretProvider:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets
        self.requested: list[str] = []

    async def get_secret(self, key: str) -> str:
        self.requested.append(key)
        if key not in self._secrets:
            raise SecretNotFoundError(key)
        return self._secrets[key]


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database": {"password": "plain-db-password"},
        "jwt": {"private_key_pem": "private", "public_key_pem": "public"},
        "encryption": {"data_key": "data-key"},
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestIsSecretReference:
    def test_recognises_the_prefix(self) -> None:
        assert is_secret_reference("secret://prod/db/password")

    def test_plain_value_is_not_a_reference(self) -> None:
        assert not is_secret_reference("hunter2")
        # A value that merely *contains* the marker isn't a reference either.
        assert not is_secret_reference("not-a-secret://thing")


class TestResolveSecrets:
    async def test_nested_reference_is_replaced_with_the_fetched_value(self) -> None:
        settings = _settings(database={"password": "secret://prod/db/password"})
        provider = FakeSecretProvider({"prod/db/password": "resolved-password"})

        resolved = await resolve_secrets(settings, provider)

        assert resolved.database.password.get_secret_value() == "resolved-password"
        assert provider.requested == ["prod/db/password"]

    async def test_plain_values_are_left_alone_and_never_fetched(self) -> None:
        """Local development must stay friction-free: plain .env values are
        used as-is, with no provider round trip at all."""
        settings = _settings()
        provider = FakeSecretProvider({})

        resolved = await resolve_secrets(settings, provider)

        assert resolved.database.password.get_secret_value() == "plain-db-password"
        assert provider.requested == []

    async def test_multiple_references_across_groups_all_resolve(self) -> None:
        settings = _settings(
            database={"password": "secret://db"},
            jwt={"private_key_pem": "secret://jwt-key", "public_key_pem": "public"},
            encryption={"data_key": "secret://enc"},
        )
        provider = FakeSecretProvider(
            {"db": "db-value", "jwt-key": "jwt-value", "enc": "enc-value"}
        )

        resolved = await resolve_secrets(settings, provider)

        assert resolved.database.password.get_secret_value() == "db-value"
        assert resolved.jwt.private_key_pem.get_secret_value() == "jwt-value"
        assert resolved.encryption.data_key.get_secret_value() == "enc-value"
        assert sorted(provider.requested) == ["db", "enc", "jwt-key"]

    async def test_missing_secret_fails_startup_rather_than_degrading(self) -> None:
        """docs/21: a missing production secret must fail at container startup,
        not at first request."""
        settings = _settings(database={"password": "secret://does/not/exist"})
        provider = FakeSecretProvider({})

        with pytest.raises(SecretNotFoundError):
            await resolve_secrets(settings, provider)

    async def test_non_secret_fields_survive_resolution(self) -> None:
        """model_copy on the nested group must not drop its other fields."""
        settings = _settings(
            database={"password": "secret://db", "host": "db.internal", "port": 6000}
        )
        provider = FakeSecretProvider({"db": "db-value"})

        resolved = await resolve_secrets(settings, provider)

        assert resolved.database.host == "db.internal"
        assert resolved.database.port == 6000
        assert resolved.database.password.get_secret_value() == "db-value"

    async def test_resolved_secret_is_still_a_secretstr(self) -> None:
        """A resolved value must not become a bare str, or it would start
        appearing in reprs and log lines."""
        settings = _settings(database={"password": "secret://db"})
        provider = FakeSecretProvider({"db": "db-value"})

        resolved = await resolve_secrets(settings, provider)

        assert isinstance(resolved.database.password, SecretStr)
        assert "db-value" not in repr(resolved.database)
