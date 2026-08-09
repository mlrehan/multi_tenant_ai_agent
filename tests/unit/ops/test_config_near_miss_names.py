"""`extra="forbid"` catches a name that matches nothing. It does not catch a
near miss, and a near miss is the mistake people actually make.

`OPENAI_API_KEY` (one underscore) is the name OpenAI's own SDK uses, so it is
the *likely* typo for `OPENAI__API_KEY`. pydantic-settings claims it -- the
lowercased key starts with the `openai` field name -- so it is never reported
as an extra; but the nested delimiter is `__`, so the remainder never resolves
to `api_key` and the value is dropped. No error, no value.

This is not hypothetical. This platform ran with knowledge-base ingestion
silently disabled while the operator had, as far as they could tell,
configured the key. Uploads were accepted and every document sat in
`processing` until someone looked at a worker log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iam_platform.core.config import Settings

pytestmark = pytest.mark.unit


def _write_env(tmp_path: Path, extra_lines: str) -> Path:
    """A minimal but *valid* env file -- the near-miss guard must be what
    fails, not a missing required field."""
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


def test_single_underscore_group_name_is_refused(tmp_path: Path) -> None:
    env = _write_env(tmp_path, "OPENAI_API_KEY=sk-typo\n")

    with pytest.raises(ValueError) as excinfo:
        Settings(_env_file=str(env))

    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    # The correction has to be in the message: knowing *that* it is wrong
    # without knowing what is right is barely better than silence.
    assert "OPENAI__API_KEY" in message


def test_the_correct_double_underscore_name_is_accepted(tmp_path: Path) -> None:
    """The positive control. Without it, a guard that rejected everything
    would pass the test above."""
    env = _write_env(tmp_path, "OPENAI__API_KEY=sk-correct\n")

    settings = Settings(_env_file=str(env))

    assert settings.openai.api_key.get_secret_value() == "sk-correct"


def test_unrelated_names_are_not_flagged(tmp_path: Path) -> None:
    """A key that merely starts with the same letters as a group must not
    trip the guard -- a false positive here refuses to boot a correctly
    configured deployment, which is worse than the bug being fixed."""
    env = _write_env(tmp_path, "STORAGE__MODE=local\nLOG_LEVEL=INFO\n")

    settings = Settings(_env_file=str(env))

    assert settings.log_level == "INFO"
    assert settings.storage.mode == "local"


@pytest.mark.parametrize("group", ["QDRANT", "COHERE", "DATABASE", "JWT"])
def test_the_guard_covers_every_group_not_just_openai(
    tmp_path: Path, group: str
) -> None:
    """The bug was found through `openai`, but nothing about it is specific to
    that group -- `DATABASE_PASSWORD` would silently leave the database
    password empty in exactly the same way."""
    env = _write_env(tmp_path, f"{group}_SOMETHING=value\n")

    with pytest.raises(ValueError, match=f"{group}_SOMETHING"):
        Settings(_env_file=str(env))


def test_a_stray_variable_is_tolerated_when_the_correct_one_is_also_set(
    tmp_path: Path,
) -> None:
    """Exporting `OPENAI_API_KEY` for some other tool is common. If the
    correctly-spelled name is also present it wins, there is no ambiguity, and
    refusing to boot the whole API over an unrelated variable would be a worse
    failure than the one this guard prevents."""
    env = _write_env(
        tmp_path, "OPENAI_API_KEY=sk-for-some-other-tool\nOPENAI__API_KEY=sk-ours\n"
    )

    settings = Settings(_env_file=str(env))

    assert settings.openai.api_key.get_secret_value() == "sk-ours"
