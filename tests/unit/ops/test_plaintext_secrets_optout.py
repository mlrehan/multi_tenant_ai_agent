"""Production refuses plaintext secrets unless someone says otherwise, once.

The rule exists to stop a deployment reaching production with its secrets in
plain environment variables **by accident** -- the shape that actually happens
is a dev `.env` copied to a server with `ENVIRONMENT` changed and nothing else.

A single-server deployment that keeps its secrets in a `chmod 600` file is a
legitimate posture, so there is an opt-out. The property worth testing is that
it is an *opt-out* and not a default: the refusal must still be what happens
when nobody has decided anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from iam_platform.core.config import Settings

pytestmark = pytest.mark.unit


def _env(tmp_path: Path, extra_lines: str) -> Path:
    env = tmp_path / "test.env"
    env.write_text(
        "DATABASE__PASSWORD=pw\n"
        "DATABASE__PLATFORM_PASSWORD=pw\n"
        "DATABASE__MIGRATOR_PASSWORD=pw\n"
        "JWT__PRIVATE_KEY_PEM=x\n"
        "JWT__PUBLIC_KEY_PEM=x\n"
        "ENCRYPTION__DATA_KEY=x\n" + extra_lines,
        encoding="utf-8",
    )
    return env


def test_production_with_plain_env_secrets_is_refused_by_default(
    tmp_path: Path,
) -> None:
    """The accident case. Nothing set beyond the environment name."""
    env = _env(tmp_path, "ENVIRONMENT=production\nSECRET_PROVIDER=env\n")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=env)
    assert "requires a real secret provider" in str(excinfo.value)


def test_the_refusal_names_the_way_out(tmp_path: Path) -> None:
    """An operator who has decided to accept this needs to be told *how*, or
    the only discoverable fix is to stop calling it production -- which would
    disable the rail everywhere rather than here."""
    env = _env(tmp_path, "ENVIRONMENT=production\nSECRET_PROVIDER=env\n")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=env)
    assert "ALLOW_PLAINTEXT_SECRETS" in str(excinfo.value)


def test_the_opt_out_permits_it(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "ENVIRONMENT=production\n"
        "SECRET_PROVIDER=env\n"
        "ALLOW_PLAINTEXT_SECRETS=true\n",
    )
    settings = Settings(_env_file=env)
    assert settings.environment == "production"
    assert settings.secret_provider == "env"


def test_the_opt_out_must_be_asked_for_explicitly(tmp_path: Path) -> None:
    """Set to false, it must behave exactly as if absent -- otherwise merely
    mentioning the name would be enough to disable the check."""
    env = _env(
        tmp_path,
        "ENVIRONMENT=production\n"
        "SECRET_PROVIDER=env\n"
        "ALLOW_PLAINTEXT_SECRETS=false\n",
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=env)


def test_the_opt_out_does_not_disable_a_real_secret_provider(
    tmp_path: Path,
) -> None:
    """It grants permission for one specific combination; it is not a global
    "skip config checks" switch. A deployment that sets both keeps using the
    secret manager it configured."""
    env = _env(
        tmp_path,
        "ENVIRONMENT=production\n"
        "SECRET_PROVIDER=aws_secrets_manager\n"
        "ALLOW_PLAINTEXT_SECRETS=true\n",
    )
    settings = Settings(_env_file=env)
    assert settings.secret_provider == "aws_secrets_manager"


def test_development_is_unaffected(tmp_path: Path) -> None:
    """The default posture for every developer must not have changed."""
    settings = Settings(_env_file=_env(tmp_path, ""))
    assert settings.environment == "development"
    assert settings.secret_provider == "env"
    assert settings.allow_plaintext_secrets is False
