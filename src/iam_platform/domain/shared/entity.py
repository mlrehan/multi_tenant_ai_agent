"""Base entity type shared by every domain module.

Deliberately minimal: identity-based equality/hash and nothing else. This
project doesn't use event sourcing, so there is no ``DomainEvent`` collection
on the base class -- add one only if/when a use case actually needs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(kw_only=True, eq=False)
class Entity:
    id: UUID

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity) or type(other) is not type(self):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self), self.id))
