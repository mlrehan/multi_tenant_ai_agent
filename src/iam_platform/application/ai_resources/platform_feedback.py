"""The platform operator's view of answer ratings across every tenant.

**Every read is audited.** Unlike the overview's counts, this returns what
visitors asked and what the assistant answered -- tenants' conversation
content, read by someone outside the tenant. The tenant's own "All
conversations" view records an audit entry when a thread is opened; this
records one per page read, naming the filters, so "who at the platform looked
at our feedback, and when" has an answer.

Gated on `platform.model_configurations.manage`, the permission behind the
operator dashboard: judging whether the platform's answers are any good is
the same job as watching what they cost. A dedicated read-only permission
would be a reasonable addition; inventing one now would be a permission
nothing else checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.answer_feedback import validate_review_filters
from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
)
from iam_platform.application.ai_resources.ports import (
    AnswerFeedbackRecord,
    AnswerFeedbackSummary,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock

MANAGE_PERMISSION = "platform.model_configurations.manage"


@dataclass(frozen=True, slots=True)
class PlatformFeedbackQuery:
    actor_user_id: str
    tenant_id: str | None = None
    rating: str | None = None
    channel: str | None = None
    limit: int = 25
    offset: int = 0


@dataclass(frozen=True, slots=True)
class PlatformFeedbackPage:
    items: list[AnswerFeedbackRecord]
    total: int
    #: One row per tenant that has any feedback, unfiltered, for the
    #: "which tenants' answers are landing badly" table.
    by_tenant: list[AnswerFeedbackSummary]


class ListPlatformAnswerFeedback:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: PlatformFeedbackQuery) -> PlatformFeedbackPage:
        actor_id = UUID(query.actor_user_id)
        tenant_filter = UUID(query.tenant_id) if query.tenant_id else None
        validate_review_filters(query.rating, query.channel, query.limit)

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(
                uow, actor_id, now=self._clock.now()
            )
            if MANAGE_PERMISSION not in state.permissions:
                raise ModelConfigurationManagementDeniedError(MANAGE_PERMISSION)

            items, total = await uow.answer_feedback.list_page(
                tenant_id=tenant_filter,
                rating=query.rating,
                channel=query.channel,
                limit=query.limit,
                offset=max(0, query.offset),
                with_identity=True,
            )
            by_tenant = await uow.answer_feedback.summarize(
                tenant_id=None, with_identity=True
            )

            # Written in the same unit of work as the read, and before
            # returning: a record of cross-tenant content access that could
            # silently fail to be written would not be a record.
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_filter,
                action="platform.answer_feedback.viewed",
                resource_type="answer_feedback",
                resource_id=None,
                result="success",
                metadata={
                    "tenant_filter": query.tenant_id,
                    "rating": query.rating,
                    "channel": query.channel,
                    "offset": query.offset,
                    "returned": len(items),
                },
            )

        by_tenant.sort(key=lambda s: (-(s.not_helpful), -(s.total), s.tenant_name or ""))
        return PlatformFeedbackPage(items=items, total=total, by_tenant=by_tenant)
