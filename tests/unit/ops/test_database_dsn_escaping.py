"""A password is data, not URL syntax.

Found by deploying: a fresh stack came up with every datastore healthy and the
migration job died on `password authentication failed for user "postgres"`.
The password in `.env` was correct. The DSN built from it was not -- `#` had
truncated it at the fragment marker.

The failure is worth describing because of how it reads from the outside: the
error names authentication, so the operator checks the password, finds it
correct, and has nowhere else to look. Nothing distinguishes "wrong password"
from "password mangled in transit".

Not a corner case, either. DEPLOYMENT.md tells operators to generate these with
`openssl rand -base64 32`, whose alphabet includes `/` and `+`.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy.engine.url import make_url

from iam_platform.core.config import DatabaseSettings

pytestmark = pytest.mark.unit

#: Every character that carries structural meaning in a URL, plus the ones
#: base64 emits. Each is a real password character somebody will eventually
#: generate.
HOSTILE = [
    "pa#ssword",  # fragment -- truncates silently, the one that was hit
    "pa%41ssword",  # percent-escape -- decodes to a *different* string
    "pa@ssword",  # userinfo separator -- shifts the host
    "pa/ssword",  # path separator; `openssl rand -base64` emits these
    "pa:ssword",  # user/password separator
    "pa?ssword",  # query
    "pa+ss=word",  # base64's other two characters
    "pa ssword",  # a space, from a hand-typed password
    "p&a$s;s,word",  # sub-delimiters
    "«pässwörd»",  # non-ASCII, from a password manager
]


def _settings(password: str) -> DatabaseSettings:
    return DatabaseSettings(
        host="postgres",
        port=5432,
        name="iam_platform",
        user="app_tenant",
        password=SecretStr(password),
        platform_user="app_platform",
        platform_password=SecretStr(password),
        migrator_user="postgres",
        migrator_password=SecretStr(password),
    )


@pytest.mark.parametrize("password", HOSTILE)
def test_the_password_survives_the_round_trip(password: str) -> None:
    """What the driver parses out must be what the operator put in.

    Asserted through SQLAlchemy's own parser rather than by inspecting the
    string, because SQLAlchemy is what actually reads it -- a test that checked
    for `%23` would pass on an encoding the driver decodes differently.
    """
    for dsn in (
        _settings(password).async_dsn,
        _settings(password).platform_dsn,
        _settings(password).migrator_dsn,
    ):
        assert make_url(dsn).password == password


@pytest.mark.parametrize("password", HOSTILE)
def test_the_host_and_database_are_not_displaced(password: str) -> None:
    """The damage is not confined to the password. An unescaped `@` moves the
    host boundary and `/` moves the database name, so the connection can end up
    pointed somewhere else entirely rather than merely failing to authenticate.
    """
    url = make_url(_settings(password).async_dsn)
    assert url.host == "postgres"
    assert url.port == 5432
    assert url.database == "iam_platform"


def test_a_username_with_special_characters_survives_too() -> None:
    """Role names are configurable, and `DATABASE__USER` is as much operator
    input as the password is."""
    settings = DatabaseSettings(
        host="postgres",
        port=5432,
        name="iam_platform",
        user="app@tenant",
        password=SecretStr("simple"),
        platform_user="app_platform",
        platform_password=SecretStr("simple"),
    )
    url = make_url(settings.async_dsn)
    assert url.username == "app@tenant"
    assert url.host == "postgres"


def test_an_ordinary_password_is_unchanged() -> None:
    """Encoding must not alter a password that needed none -- otherwise the fix
    would break every deployment that was working."""
    dsn = _settings("SimplePassword123").async_dsn
    assert dsn == (
        "postgresql+asyncpg://app_tenant:SimplePassword123@postgres:5432/iam_platform"
    )
