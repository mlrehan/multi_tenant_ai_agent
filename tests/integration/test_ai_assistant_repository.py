"""`AiAssistantRepository.save()` against real Postgres.

Exists because a real defect lived here invisibly: `save()`'s SQL `UPDATE`
statement never listed `model_configuration_id`, so `UpdateAssistant` -- and
the console's "Edit assistant" model picker built on it -- silently could not
change an assistant's model after creation. Every existing unit test for
`UpdateAssistant` drives it against an in-memory fake repository, which has no
SQL statement to be missing a column from and so could never have caught
this. Found only by running the real thing: `AnswerQuestionQuery.assistant_id`
resolved a *stale* model configuration in a live end-to-end check, because the
edit that was supposed to change it had never taken.

Two separate sessions on purpose, matching the shape of a real request
sequence (edit, then a later request reads it back) -- a single session's
identity map could echo the Python object's in-memory value and pass even if
the `UPDATE` itself never reached the row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from iam_platform.domain.ai_resources.entities import AiAssistant, AssistantStatus, ResourceVisibility
from iam_platform.infrastructure.db.session import build_session_factory
from iam_platform.infrastructure.db.unit_of_work import SqlAiResourceUnitOfWork

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed(migrator_engine: AsyncEngine) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    """user -> tenant -> membership -> two model configurations."""
    user_id, tenant_id, membership_id, config_a, config_b = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4(),
    )
    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :ss)"
            ),
            {"id": str(user_id), "email": f"assistant-repo-{user_id}@example.com", "ss": str(uuid4())},
        )
        await conn.execute(
            text(
                "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                "VALUES (:id, :slug, 'assistant-repo', 'active', :owner)"
            ),
            {"id": str(tenant_id), "slug": f"assistant-repo-{tenant_id}", "owner": str(user_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO tenant_memberships "
                "(id, tenant_id, user_id, status, is_default, metadata, created_at, updated_at) "
                "VALUES (:id, :t, :u, 'active', true, '{}'::jsonb, now(), now())"
            ),
            {"id": str(membership_id), "t": str(tenant_id), "u": str(user_id)},
        )
        for config_id, name in ((config_a, "model-a"), (config_b, "model-b")):
            await conn.execute(
                text(
                    "INSERT INTO model_configurations "
                    "(id, tenant_id, model_name, parameters, created_at, updated_at) "
                    "VALUES (:id, NULL, :name, '{}'::jsonb, now(), now())"
                ),
                {"id": str(config_id), "name": name},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_model_configurations "
                    "(id, tenant_id, model_configuration_id, granted_by_user_id) "
                    "VALUES (:id, :t, :c, :u)"
                ),
                {"id": str(uuid4()), "t": str(tenant_id), "c": str(config_id), "u": str(user_id)},
            )
    return user_id, tenant_id, membership_id, config_a, config_b


class TestSaveUpdatesTheModelConfiguration:
    async def test_a_changed_model_configuration_id_actually_persists(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        user_id, tenant_id, membership_id, config_a, config_b = await _seed(migrator_engine)
        session_factory = build_session_factory(engine)

        assistant_id = uuid4()
        async with SqlAiResourceUnitOfWork(session_factory, user_id=user_id, tenant_id=tenant_id) as uow:
            await uow.assistants.add(
                AiAssistant(
                    id=assistant_id,
                    tenant_id=tenant_id,
                    name="Support Bot",
                    owner_membership_id=membership_id,
                    visibility=ResourceVisibility.TENANT,
                    model_configuration_id=config_a,
                    status=AssistantStatus.PUBLISHED,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

        # A fresh session for the edit -- the defect this guards against was
        # in the SQL `UPDATE`, not in anything a single session's object
        # identity could paper over.
        async with SqlAiResourceUnitOfWork(session_factory, user_id=user_id, tenant_id=tenant_id) as uow:
            assistant = await uow.assistants.get_by_id(assistant_id)
            assert assistant is not None
            assistant.model_configuration_id = config_b
            await uow.assistants.save(assistant)

        # And a third session to read it back -- proving the row itself
        # changed, not merely the in-memory object this test is holding.
        async with SqlAiResourceUnitOfWork(session_factory, user_id=user_id, tenant_id=tenant_id) as uow:
            reloaded = await uow.assistants.get_by_id(assistant_id)
            assert reloaded is not None
            assert reloaded.model_configuration_id == config_b
