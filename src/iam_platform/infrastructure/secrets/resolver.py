"""Resolves ``secret://`` references in a constructed ``Settings`` object --
docs/21-configuration-and-secrets.md, "Resolution order at startup".

**Why this exists (Phase 9 finding).** The ``SecretProvider`` port and
``EnvSecretProvider`` shipped in Phase 5, and docs/21 describes the resolution
step in detail -- but nothing ever called it. A production deploy setting
``DATABASE__PASSWORD=secret://prod/db/password`` would have used that literal
string as the password. Combined with ``Settings.model_post_init`` refusing to
start when ``environment=production`` and ``secret_provider=env``, the service
could not correctly start in production at all.

Resolution walks the settings tree and replaces any ``SecretStr`` whose value
is a ``secret://`` reference with the fetched value. Plain values pass through
untouched, so local development stays friction-free.

The walk is deliberately explicit about *where* it looks (the root model and
its nested ``BaseModel`` groups, one level deep) rather than recursing
arbitrarily: the settings shape is known and flat by design
(docs/21-configuration-and-secrets.md), and an unbounded recursion over
arbitrary Pydantic models would be harder to reason about for something on the
startup-critical path.
"""

from __future__ import annotations

from pydantic import BaseModel, SecretStr

from iam_platform.core.config import Settings
from iam_platform.infrastructure.secrets.base import SecretProvider

SECRET_REFERENCE_PREFIX = "secret://"


def is_secret_reference(value: str) -> bool:
    return value.startswith(SECRET_REFERENCE_PREFIX)


def _reference_key(value: str) -> str:
    return value.removeprefix(SECRET_REFERENCE_PREFIX)


async def _resolve_model(model: BaseModel, provider: SecretProvider) -> dict[str, SecretStr]:
    """Returns ``{field_name: resolved_value}`` for every ``SecretStr`` field on
    ``model`` that currently holds a reference. Empty when nothing needs
    resolving, which is the common development case."""
    resolved: dict[str, SecretStr] = {}
    for field_name in type(model).model_fields:
        value = getattr(model, field_name, None)
        if not isinstance(value, SecretStr):
            continue
        raw = value.get_secret_value()
        if not is_secret_reference(raw):
            continue
        fetched = await provider.get_secret(_reference_key(raw))
        resolved[field_name] = SecretStr(fetched)
    return resolved


async def resolve_secrets(settings: Settings, provider: SecretProvider) -> Settings:
    """Returns a settings object with every ``secret://`` reference replaced.

    Pydantic models are mutated in place via ``model_copy(update=...)`` on the
    nested groups rather than rebuilt from scratch -- reconstructing ``Settings``
    would re-read the environment and re-run validators, undoing the resolution
    we just performed.

    Raises whatever the provider raises (typically ``SecretNotFoundError``) --
    a missing production secret must fail the deploy at container startup, not
    at first request (docs/21).
    """
    root_updates: dict[str, object] = {}

    for field_name in type(settings).model_fields:
        value = getattr(settings, field_name, None)

        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if is_secret_reference(raw):
                root_updates[field_name] = SecretStr(
                    await provider.get_secret(_reference_key(raw))
                )
            continue

        if isinstance(value, BaseModel):
            nested_updates = await _resolve_model(value, provider)
            if nested_updates:
                root_updates[field_name] = value.model_copy(update=nested_updates)

    if not root_updates:
        return settings
    return settings.model_copy(update=root_updates)
