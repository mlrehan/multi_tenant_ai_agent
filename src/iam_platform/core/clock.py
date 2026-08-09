"""``now()`` indirection so application/domain code never calls ``datetime.now`` directly.

Tests inject a ``FixedClock`` to get deterministic timestamps instead of monkeypatching
the standard library, per docs/19-folder-structure.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed
