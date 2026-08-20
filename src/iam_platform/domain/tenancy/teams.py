"""Teams a conversation can be handed off to.

**These are the tenant's own teams, and nothing here ships with names in it.**
"Admissions", "Accounts" and "General Support" are what a nursery happens to
call its teams; a different tenant will have different ones, and a platform
that hard-codes the list decides on their behalf how their organisation is
shaped. The handoff menu a visitor sees is built from these rows and only these
rows -- if a tenant has configured none, the AI says it cannot transfer rather
than offering a team that does not exist.

`tenant_memberships.team_id` has existed since Phase 7 as a *visibility*
scope with no table behind it, which is why assistant department/team
visibility has been rendered disabled in the console. This table is that
missing side, and joining the two is deliberately left alone: visibility
scoping and handoff routing are different questions, and merging them now
would silently change who can see every team-scoped assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from iam_platform.domain.shared.entity import Entity

MAX_TEAM_NAME_CHARS = 100


@dataclass(kw_only=True)
class TenantTeam(Entity):
    tenant_id: UUID
    name: str
    description: str | None = None
    #: An inactive team keeps its history and its members but stops being
    #: offered to visitors. Deleting instead would orphan every conversation
    #: routed to it -- and those are exactly the records someone reviewing a
    #: complaint needs.
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a team needs a name")
        if len(self.name) > MAX_TEAM_NAME_CHARS:
            raise ValueError(
                f"a team name must be {MAX_TEAM_NAME_CHARS} characters or fewer"
            )

    def rename(self, name: str, *, now: datetime) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("a team needs a name")
        self.name = cleaned[:MAX_TEAM_NAME_CHARS]
        self.updated_at = now

    def deactivate(self, *, now: datetime) -> None:
        self.is_active = False
        self.updated_at = now

    def activate(self, *, now: datetime) -> None:
        self.is_active = True
        self.updated_at = now


@dataclass(kw_only=True)
class TenantTeamMember(Entity):
    """Which members staff a team. Composite-keyed to its tenant in the
    database, so a membership from another tenant cannot be added whatever id
    a request carries."""

    tenant_id: UUID
    team_id: UUID
    membership_id: UUID
    created_at: datetime
