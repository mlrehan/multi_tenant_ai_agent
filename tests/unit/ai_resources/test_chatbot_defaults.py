"""The shipped nursery defaults, and where they may and may not apply.

A default is only useful if it is a *fallback*. The failure worth guarding is
the opposite one: a default that quietly overrides something a tenant typed, or
that introduces one nursery's assistant using another nursery's name. Both
would be invisible from the console -- the form would show the tenant's own
words while the model followed the platform's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.prompt_layers import PromptLayers
from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_AVOID,
    DEFAULT_COMPANY_NAME,
    AssistantBehaviour,
    TenantChatbotSettings,
    default_company_description,
    default_role,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _settings(**kwargs: object) -> TenantChatbotSettings:
    return TenantChatbotSettings(
        id=uuid4(),
        tenant_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


class TestTheDefaultsFit:
    def test_a_brief_longer_than_the_old_cap_is_accepted(self) -> None:
        """Role and avoid are deliberately unbounded now. The caps were a guess
        at how long a brief needs to be, and the guess was wrong twice: 1000
        could not hold the platform's own shipped default, and 2000 was the
        same guess with a bigger number. The real bound is the model's context
        window, which the provider enforces and which moves as models change --
        a fixed limit here could only ever disagree with it."""
        long_brief = "Answer questions about opening hours. " * 200
        behaviour = AssistantBehaviour(role=long_brief, avoid=long_brief)
        assert behaviour.role == long_brief
        assert behaviour.avoid == long_brief

    def test_the_shipped_brief_is_what_an_unconfigured_assistant_uses(self) -> None:
        assert AssistantBehaviour().role == default_role(DEFAULT_COMPANY_NAME)
        assert AssistantBehaviour().avoid == DEFAULT_AVOID


class TestTheyOnlyEverFillAnEmptyField:
    def test_a_tenants_own_name_wins(self) -> None:
        settings = _settings(company_name="Bright Beginnings")
        assert settings.resolved_company_name("Acme Ltd") == "Bright Beginnings"

    def test_the_account_display_name_is_preferred_over_the_shipped_name(self) -> None:
        """Someone chose the account's display name; nobody chose the shipped
        one. A default that outranked a real choice would be an override."""
        assert _settings().resolved_company_name("Acme Ltd") == "Acme Ltd"

    def test_the_shipped_name_applies_only_when_both_are_empty(self) -> None:
        assert _settings().resolved_company_name("") == DEFAULT_COMPANY_NAME

    def test_whitespace_is_not_a_configured_name(self) -> None:
        assert _settings(company_name="   ").resolved_company_name("") == (
            DEFAULT_COMPANY_NAME
        )

    def test_a_tenants_own_description_wins(self) -> None:
        settings = _settings(company_description="We are a small village nursery.")
        assert settings.resolved_company_description("") == (
            "We are a small village nursery."
        )

    def test_an_unset_description_falls_back_to_the_shipped_one(self) -> None:
        assert _settings().resolved_company_description("") == (
            default_company_description(DEFAULT_COMPANY_NAME)
        )


class TestTheDefaultsAreNamedForTheRightNursery:
    def test_the_description_is_written_for_this_company(self) -> None:
        """The templates carry a placeholder rather than a baked-in name. A
        default that described every tenant as the shipped nursery would be
        worse than no default at all -- it reads as correct and is wrong."""
        text = default_company_description("Bright Beginnings")
        assert "Bright Beginnings is a London-based day nursery" in text
        assert DEFAULT_COMPANY_NAME not in text

    def test_the_role_is_written_for_this_company(self) -> None:
        text = default_role("Bright Beginnings")
        assert text.startswith("Bright Beginnings AI Assistant")
        assert DEFAULT_COMPANY_NAME not in text

    def test_no_placeholder_survives_into_the_prompt(self) -> None:
        """A stray `{company}` would be shown to a parent verbatim."""
        assert "{company}" not in default_role("Bright Beginnings")
        assert "{company}" not in default_company_description("Bright Beginnings")

    def test_the_restrictions_carry_no_company_name(self) -> None:
        """What the assistant must not do is the same whoever it speaks for."""
        assert DEFAULT_COMPANY_NAME not in DEFAULT_AVOID
        assert "{company}" not in DEFAULT_AVOID


class TestThePromptUsesThem:
    def test_an_unconfigured_tenant_still_gets_a_described_company(self) -> None:
        layers = PromptLayers.from_settings(_settings(), tenant_display_name="Acme Ltd")
        assert layers.company_name == "Acme Ltd"
        assert "Acme Ltd is a London-based day nursery" in layers.company_description
        assert layers.role.startswith("Acme Ltd AI Assistant")

    def test_a_tenant_with_no_settings_row_at_all_still_gets_one(self) -> None:
        """The row is created lazily, so the very first question a visitor asks
        can arrive before the tenant has ever opened the Chatbot screen."""
        layers = PromptLayers.from_settings(None, tenant_display_name="Acme Ltd")
        assert "Acme Ltd is a London-based day nursery" in layers.company_description
        assert layers.role.startswith("Acme Ltd AI Assistant")

    def test_a_configured_assistant_is_untouched(self) -> None:
        layers = PromptLayers.from_settings(
            _settings(company_description="We are a small village nursery."),
            tenant_display_name="Acme Ltd",
            role="Answer only questions about opening hours.",
            avoid="Never discuss fees.",
        )
        assert layers.company_description == "We are a small village nursery."
        assert layers.role == "Answer only questions about opening hours."
        assert layers.avoid == "Never discuss fees."
