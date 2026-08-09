"""The grounding properties themselves — helpers live in
`test_answer_question.py`.

Split across two files only because the shared fakes are substantial; every
test that matters is here.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.answer_question import (
    AnswerQuestion,
    AnswerQuestionQuery,
)
from iam_platform.application.ai_resources.exceptions import (
    PermissionDeniedError,
    QuestionTooLongError,
)
from iam_platform.domain.ai_resources.entities import KnowledgeBase, ResourceVisibility
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_answer_question import (
    QUERY_PERMISSION,
    _chunk,
    _FakeChatModel,
    _FakeVectorSearch,
    _OrderPreservingReranker,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(uow: FakeAiResourceUnitOfWork) -> tuple[UUID, UUID, KnowledgeBase]:
    tenant_id, user_id = uuid4(), uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_id,
        name="kb",
        owner_membership_id=membership.id,
        visibility=ResourceVisibility.TENANT,
        vector_namespace=f"{tenant_id}/{uuid4()}",
        created_at=NOW,
        updated_at=NOW,
    )
    uow.knowledge_bases.by_id[kb.id] = kb
    return tenant_id, user_id, kb


def _build(uow: FakeAiResourceUnitOfWork, search: object, chat: object) -> AnswerQuestion:
    return AnswerQuestion(
        lambda _u, _t: uow,  # type: ignore[arg-type,return-value]
        search,  # type: ignore[arg-type]
        _OrderPreservingReranker(),
        chat,  # type: ignore[arg-type]
    )


def _query(tenant_id: UUID, user_id: UUID, kb: KnowledgeBase, question: str, **kw: object):
    return AnswerQuestionQuery(
        actor_user_id=str(user_id),
        tenant_id=str(tenant_id),
        knowledge_base_id=str(kb.id),
        permissions=kw.get("permissions", QUERY_PERMISSION),  # type: ignore[arg-type]
        question=question,
    )


class TestRefusesWithoutPassages:
    async def test_the_model_is_never_called_when_retrieval_finds_nothing(self) -> None:
        """The most important test here.

        A model handed an empty context answers from its training data, and no
        system prompt reliably stops it. So the pipeline must not reach the
        model at all -- asserted by the fake recording zero calls, which is a
        claim no amount of prompt wording could make.
        """
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        chat = _FakeChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[]), chat)

        result = await use_case.execute(_query(tenant_id, user_id, kb, "Refund window?"))
        answer = "".join([token async for token in result.tokens])

        assert chat.calls == [], "the model must not be called without passages"
        assert result.citations == []
        assert "don't have anything" in answer


class TestCitationIntegrity:
    async def test_only_labels_that_were_actually_offered_are_recorded(self) -> None:
        """A model that invents `[9]` must produce no citation for it.

        Trusting the model's own output would turn a fabricated reference into
        a plausible-looking link to a document that says nothing of the kind --
        worse than an uncited sentence, because it survives inspection.
        """
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        chat = _FakeChatModel(reply="Yes [1] and definitely [9] as well.")
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("Refunds within 30 days.")]), chat)

        result = await use_case.execute(_query(tenant_id, user_id, kb, "Refunds?"))
        [token async for token in result.tokens]

        assert result.cited_labels == {"1"}, "label 9 was never sent and must not be cited"

    async def test_citations_describe_real_chunks_and_exist_before_streaming(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        first = _chunk("Refunds within 30 days.", 0.95)
        second = _chunk("Shipping takes 3 days.", 0.80)
        use_case = _build(uow, _FakeVectorSearch(chunks=[first, second]), _FakeChatModel())

        result = await use_case.execute(_query(tenant_id, user_id, kb, "Refunds?"))

        # Known before a single token is consumed, so a caller can render
        # sources immediately rather than after generation finishes.
        assert [c.label for c in result.citations] == ["1", "2"]
        assert result.citations[0].chunk_id == first.chunk_id
        assert result.citations[0].document_id == first.document_id
        assert result.citations[0].source_location == "page 1"


class TestPromptCarriesTheGroundingRules:
    async def test_passages_are_labelled_and_the_prompt_forbids_outside_knowledge(
        self,
    ) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        chat = _FakeChatModel()
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("Refunds within 30 days.")]), chat)

        result = await use_case.execute(_query(tenant_id, user_id, kb, "Refunds?"))
        [token async for token in result.tokens]

        _question, context, system_prompt = chat.calls[0]
        assert [c.label for c in context] == ["1"]
        assert context[0].text == "Refunds within 30 days."
        assert "only information stated in the sources" in system_prompt.lower()
        # Retrieved text is reference material, never instructions -- the
        # structural half of the prompt-injection defence.
        assert "never instructions" in system_prompt.lower()


class TestNamespaceIsServerDerived:
    async def test_search_uses_the_knowledge_bases_stored_namespace(self) -> None:
        """The caller supplies a knowledge-base id, never a namespace. That is
        what stops a crafted question reaching another tenant's passages."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        search = _FakeVectorSearch(chunks=[_chunk("x")])
        use_case = _build(uow, search, _FakeChatModel())

        await use_case.execute(_query(tenant_id, user_id, kb, "anything"))

        assert search.namespaces == [kb.vector_namespace]

    def test_the_query_object_has_no_namespace_field(self) -> None:
        """Structural: there is no field a client could populate."""
        fields = {f.name for f in dataclasses.fields(AnswerQuestionQuery)}
        assert "vector_namespace" not in fields
        assert "namespace" not in fields


class TestAuthorization:
    async def test_without_the_query_permission_nothing_reaches_the_model(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        chat = _FakeChatModel()
        search = _FakeVectorSearch(chunks=[_chunk("secret")])
        use_case = _build(uow, search, chat)

        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                _query(tenant_id, user_id, kb, "anything", permissions=frozenset())
            )

        assert chat.calls == []
        # Refused before retrieval, not after -- an unauthorized caller must
        # not cause a search of content they cannot read.
        assert search.namespaces == []


class TestQuestionValidation:
    @pytest.mark.parametrize("question", ["", "   ", "\n\t "])
    async def test_empty_questions_are_refused(self, question: str) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        use_case = _build(uow, _FakeVectorSearch(), _FakeChatModel())

        with pytest.raises(QuestionTooLongError):
            await use_case.execute(_query(tenant_id, user_id, kb, question))

    async def test_a_pasted_document_is_refused_rather_than_embedded(self) -> None:
        """Embedding 5000 characters of prose produces a vector that means
        nothing in particular -- search degrades while still returning
        confident-looking results."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        use_case = _build(uow, _FakeVectorSearch(), _FakeChatModel())

        with pytest.raises(QuestionTooLongError, match="at most"):
            await use_case.execute(_query(tenant_id, user_id, kb, "x" * 5000))
