"""Domain-level exceptions -- violated invariants and illegal state transitions only.

Not HTTP-aware, not persistence-aware. The ``api`` layer's exception handlers
map these to status codes (see api/exception_handlers.py).
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class InvariantViolationError(DomainError):
    """A value or combination of values violates a domain rule."""


class InvalidStateTransitionError(DomainError):
    """An entity was asked to transition into a state it cannot reach from its current one."""
