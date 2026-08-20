"""Retention actually deletes, and deletes the right things.

The number on the settings screen is only a retention policy if something
enforces it. These tests pin the two decisions that make it one: an
unconfigured tenant is still governed (by the default), and the cutoff is
computed from the tenant's own window rather than a platform constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.purge_conversations import (
    PurgeExpiredConversations,
)
from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_RETENTION_DAYS,
    TenantChatbotSettings,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
TENANT = uuid4()


class _FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass
class _FakeSettings:
    settings: TenantChatbotSettings | None

    async def get_for_tenant(self, tenant_id: UUID) -> TenantChatbotSettings | None:
        del tenant_id
        return self.settings


@dataclass
class _FakeHandoff:
    deleted: int = 4
    cutoffs: list[datetime] = field(default_factory=list)
    tenants: list[UUID] = field(default_factory=list)

    async def purge_expired_conversations(
        self, *, tenant_id: UUID, older_than: datetime
    ) -> int:
        self.tenants.append(tenant_id)
        self.cutoffs.append(older_than)
        return self.deleted


@dataclass
class _FakeAudit:
    records: list[dict[str, object]] = field(default_factory=list)

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


class _FakeUow:
    def __init__(self, settings: TenantChatbotSettings | None, handoff: _FakeHandoff):
        self.chatbot_settings = _FakeSettings(settings)
        self.handoff = handoff
        self.audit = _FakeAudit()

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _settings(days: int) -> TenantChatbotSettings:
    return TenantChatbotSettings(
        id=uuid4(),
        tenant_id=TENANT,
        conversation_retention_days=days,
        created_at=NOW,
        updated_at=NOW,
    )


def _run(settings: TenantChatbotSettings | None, handoff: _FakeHandoff):
    uow = _FakeUow(settings, handoff)

    def factory(user_id: UUID, tenant_id: UUID) -> _FakeUow:
        del user_id, tenant_id
        return uow

    use_case = PurgeExpiredConversations(factory, _FixedClock())  # type: ignore[arg-type]
    return use_case, uow


class TestRetentionIsEnforced:
    async def test_the_cutoff_comes_from_the_tenants_own_window(self) -> None:
        handoff = _FakeHandoff()
        use_case, _ = _run(_settings(7), handoff)

        result = await use_case.execute(tenant_id=TENANT)

        assert handoff.cutoffs == [NOW - timedelta(days=7)]
        assert result.retention_days == 7
        assert result.deleted == 4

    async def test_a_tenant_with_no_settings_row_is_still_governed(self) -> None:
        """The tenants who never opened the screen are exactly the ones who
        must not be exempt from retention."""
        handoff = _FakeHandoff()
        use_case, _ = _run(None, handoff)

        result = await use_case.execute(tenant_id=TENANT)

        assert result.retention_days == DEFAULT_RETENTION_DAYS
        assert handoff.cutoffs == [NOW - timedelta(days=DEFAULT_RETENTION_DAYS)]

    async def test_the_purge_is_scoped_to_the_tenant_it_was_asked_about(self) -> None:
        handoff = _FakeHandoff()
        use_case, _ = _run(_settings(30), handoff)

        await use_case.execute(tenant_id=TENANT)

        assert handoff.tenants == [TENANT]

    async def test_a_deletion_is_audited(self) -> None:
        """The rows are gone; the record that they went must not be."""
        handoff = _FakeHandoff(deleted=9)
        use_case, uow = _run(_settings(30), handoff)

        await use_case.execute(tenant_id=TENANT)

        assert len(uow.audit.records) == 1
        entry = uow.audit.records[0]
        assert entry["action"] == "tenant.conversations.purged"
        assert entry["metadata"]["deleted"] == 9  # type: ignore[index]

    async def test_a_sweep_that_deletes_nothing_writes_no_audit_noise(self) -> None:
        handoff = _FakeHandoff(deleted=0)
        use_case, uow = _run(_settings(30), handoff)

        result = await use_case.execute(tenant_id=TENANT)

        assert result.deleted == 0
        assert uow.audit.records == []
