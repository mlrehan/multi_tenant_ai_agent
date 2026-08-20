"""Chatbot configuration, split across the three levels that actually own it.

The split is not cosmetic -- each level is the *only* place its data can
correctly live, and putting a field at the wrong level makes it either
unshareable or unoverridable:

* **Tenant** (`TenantChatbotSettings`) -- one company, one answer. Company name,
  description and industry describe the organisation, not a bot; the master AI
  on/off switch and the handoff policy are decisions a tenant makes once and
  must not be able to contradict per-widget. Duplicating these onto widgets
  would let two widgets disagree about whether the tenant permits human
  handoff, which is a policy question with exactly one right answer.
* **Assistant** (`AssistantBehaviour`) -- how the AI thinks. Role, avoid-rules,
  personality and response length are the assistant's brief, and a tenant may
  reasonably run a strict admissions assistant beside a chatty general one.
* **Widget** (`WidgetPresentation`) -- how the AI looks on one page. Name,
  title, avatar and quick replies are per-embed, because a nursery may run one
  widget on its parent portal and another on its public site.

**Every string on this page is tenant-editable, and therefore untrusted.** The
prompt builder fences all of it and states its standing explicitly; none of it
can reach the model as an instruction that outranks the platform's own rules.
That is why `Personality` and `ResponseLength` are enums mapped to internal
text rather than free strings: a free-form "tone" field is an unbounded
injection surface for a benefit (arbitrary tone wording) nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from iam_platform.domain.shared.entity import Entity

#: Length caps, enforced here *and* by a database CHECK. The domain refuses
#: first so the caller gets a useful message; the constraint means a migration
#: or a direct write cannot store something the prompt builder would then have
#: to truncate silently.
MAX_COMPANY_DESCRIPTION_CHARS = 2000
MAX_INDUSTRY_CHARS = 100
MAX_DIRECT_TEXT_CHARS = 5000

#: Conversation retention, in days. Mirrored by a database CHECK -- the domain
#: refuses first so the tenant gets a useful message, and the constraint means
#: a direct write cannot store a value the purge would then have to interpret.
DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650

DEFAULT_CHATBOT_NAME = "Nursery Support Assistant"
DEFAULT_CHATBOT_TITLE = "Parent & Nursery Support"
DEFAULT_INDUSTRY = "Early Years Education and Childcare (UK Nursery / Preschool)"

#: Shipped with the platform rather than fetched, so a tenant that configures
#: nothing still gets a coherent widget and no request leaves the page to a
#: third-party avatar host. The value is an *asset key*, resolved by the widget
#: to an inline SVG -- deliberately not a URL, so a tenant cannot point the
#: avatar at an arbitrary external origin (a tracking pixel on every visitor,
#: served from the tenant's own customers' browsers).
DEFAULT_AVATAR_KEY = "nursery-default"
AVATAR_KEYS = ("nursery-default", "nursery-bear", "nursery-star", "nursery-leaf")

#: The opening prompts a widget offers before the visitor has typed anything.
#:
#: Defined here, and returned to the widget by the session endpoint, so the
#: console's preview and the embedded script cannot show different pills. They
#: previously did: the preview hard-coded three labels the widget had no way to
#: know about, which is exactly the "the real thing does not look like the
#: preview" complaint this constant exists to prevent.
#:
#: Not tenant-editable yet, deliberately. A per-widget list is a reasonable
#: future column; inventing one now would mean a settings field with no
#: migration behind it.
DEFAULT_QUICK_REPLIES = ("Admissions", "Fees & funding", "Opening hours")

#: Offered alongside them **only when the tenant permits handoff**. Showing it
#: otherwise would advertise a transfer that cannot happen -- the visitor
#: presses it, the question reaches the model instead, and they are told
#: nothing about why nobody came.
HANDOFF_QUICK_REPLY = "Speak to a person"

#: The nursery the platform ships configured for, used when a tenant has
#: not named their own. Deliberately a *fallback*, never an override: a
#: tenant who types their own name keeps it, and this only fills a field
#: nobody has set.
DEFAULT_COMPANY_NAME = "Falgoon Little Star"

#: The token the shipped templates below substitute the resolved company
#: name into. A placeholder rather than a baked-in name, because the same
#: paragraphs are correct for any nursery once the name is right -- and a
#: default introducing every tenant's assistant as the wrong nursery would
#: be worse than no default at all.
COMPANY_PLACEHOLDER = "{company}"

_DEFAULT_COMPANY_DESCRIPTION_TEMPLATE = "\n\n".join(
    (
        (
        "{company} is a London-based day nursery providing early years "
        "childcare, education and family support within a safe, welcoming, "
        "inclusive and nurturing environment."
        ),
        (
        "The nursery supports children through age-appropriate care, play, "
        "daily routines and learning experiences designed around each child's "
        "stage of development. Its early years provision follows the "
        "principles of the Early Years Foundation Stage (EYFS), supporting "
        "areas such as communication and language, physical development, "
        "personal, social and emotional development, literacy, mathematics, "
        "understanding the world and expressive arts and design."
        ),
        (
        "{company} works closely with parents and guardians throughout their "
        "child's nursery journey, from initial enquiries, nursery visits and "
        "registration through settling-in, daily care, development "
        "discussions and transitions."
        ),
        (
        "Nursery services and information may include age-based rooms, "
        "childcare sessions, opening hours, fees, funded childcare, "
        "admissions, meals and dietary requirements, allergies, sleep and "
        "toileting routines, learning activities, SEND support, safeguarding, "
        "illness and medication procedures, holidays, events and family "
        "communications."
        ),
        (
        "The nursery places strong emphasis on safeguarding, child welfare, "
        "inclusion, confidentiality, respectful communication and positive "
        "partnerships with families."
        ),
        (
        "The {company} AI Assistant acts as a digital front desk. It provides "
        "approved general information, helps families navigate nursery "
        "services, supports initial enquiries and passes sensitive, child- "
        "specific, safeguarding-related or decision-based matters to "
        "authorized nursery staff."
        ),
    )
)

_DEFAULT_ROLE_TEMPLATE = "\n\n".join(
    (
        (
        "{company} AI Assistant is the nursery's digital front-desk "
        "assistant. Its role is to help parents, guardians and prospective "
        "families quickly find accurate nursery information and guide them to "
        "the appropriate next step."
        ),
        (
        "It can answer general enquiries about admissions, age groups and "
        "rooms, opening hours, sessions, fees, funded childcare, nursery "
        "visits, settling-in, daily routines, meals and allergies, sleep, "
        "learning and development, the EYFS approach, SEND support, "
        "safeguarding policies, key-person arrangements, holidays, events, "
        "what children should bring, and other approved nursery policies."
        ),
        (
        "It can explain registration and enquiry processes, collect basic "
        "contact and childcare requirements where appropriate, and direct "
        "complex or child-specific matters to authorized nursery staff."
        ),
        (
        "Responses should be warm, reassuring, professional, concise and "
        "family-friendly, using only approved nursery information and "
        "connected knowledge sources."
        ),
    )
)

#: No placeholder: what the assistant must not do is the same whoever it
#: speaks for, and a company name inside a restriction reads as though the
#: restriction were about that company rather than about the assistant.
DEFAULT_AVOID = "\n\n".join(
    (
        (
        "The chatbot must not make, confirm or guarantee decisions about "
        "nursery places, waiting-list positions, fees, funding eligibility, "
        "discounts, refunds, start dates or bookings unless explicitly "
        "confirmed by an authorized nursery system or staff member."
        ),
        (
        "It must not provide medical, legal or safeguarding judgments, "
        "diagnose a child, recommend medication, or replace emergency "
        "services or qualified professionals."
        ),
        (
        "It must never disclose confidential information about any child, "
        "parent, guardian, employee or another family. It should not request "
        "unnecessary sensitive information, passwords, payment-card details "
        "or detailed health or safeguarding records through general chat."
        ),
        (
        "It must not invent policies, availability, staff information, "
        "regulatory information or answers that are not supported by approved "
        "sources."
        ),
        (
        "Safeguarding concerns, complaints, accidents, emergencies, "
        "medication issues and sensitive child-specific matters must be "
        "escalated to authorized nursery staff. If information is uncertain, "
        "the chatbot should clearly say so and offer human assistance."
        ),
    )
)

def default_role(company_name: str) -> str:
    """The shipped role brief, named for this nursery."""
    return _DEFAULT_ROLE_TEMPLATE.replace(COMPANY_PLACEHOLDER, company_name)


def default_company_description(company_name: str) -> str:
    """The shipped company description, named for this nursery."""
    return _DEFAULT_COMPANY_DESCRIPTION_TEMPLATE.replace(
        COMPANY_PLACEHOLDER, company_name
    )


#: Rendered with the shipped name so the module-level constant stays a
#: plain string for callers that have no tenant in hand. Anything that
#: *does* know the tenant calls `default_role()` instead, or the prompt
#: introduces the assistant as the wrong nursery.
DEFAULT_ROLE = default_role(DEFAULT_COMPANY_NAME)


class Personality(StrEnum):
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    REASSURING = "reassuring"
    PROFESSIONAL = "professional"


class ResponseLength(StrEnum):
    CONCISE = "concise"
    BALANCED = "balanced"
    DETAILED = "detailed"


#: **The enum label never reaches the model.** These mappings do. A stored
#: value that is not a known enum member resolves to the neutral default rather
#: than being passed through, so a hand-written database row cannot smuggle
#: instruction text into the prompt through a field the UI presents as a
#: four-way dropdown.
_PERSONALITY_INSTRUCTIONS: dict[Personality, str] = {
    Personality.NEUTRAL: (
        "Write in a plain, even tone. State facts directly without embellishment."
    ),
    Personality.FRIENDLY: (
        "Write warmly and conversationally. Use approachable everyday language "
        "and welcome the reader, while staying accurate."
    ),
    Personality.REASSURING: (
        "Write calmly and supportively. Many readers are parents who may be "
        "anxious; acknowledge concerns briefly before answering, and be gentle "
        "about uncertainty."
    ),
    Personality.PROFESSIONAL: (
        "Write formally and precisely, as a nursery administrator would in "
        "written correspondence. Avoid contractions and casual phrasing."
    ),
}

#: Guidance, not a hard cutoff. Truncating a generated answer at a token count
#: produces a sentence that stops mid-word, which reads as a broken product;
#: instructing the model to be brief produces a brief *answer*.
_LENGTH_INSTRUCTIONS: dict[ResponseLength, str] = {
    ResponseLength.CONCISE: (
        "Answer in at most two or three short sentences. Give the essential "
        "answer only, then stop."
    ),
    ResponseLength.BALANCED: (
        "Answer in a short paragraph. Include the key supporting detail, but do "
        "not pad."
    ),
    ResponseLength.DETAILED: (
        "Answer thoroughly. Use short bullet points for lists of options, "
        "requirements or steps, and cover relevant caveats."
    ),
}


def personality_instruction(value: Personality | str | None) -> str:
    return _PERSONALITY_INSTRUCTIONS[_coerce(value, Personality, Personality.NEUTRAL)]


def response_length_instruction(value: ResponseLength | str | None) -> str:
    return _LENGTH_INSTRUCTIONS[
        _coerce(value, ResponseLength, ResponseLength.BALANCED)
    ]


def _coerce[E: StrEnum](value: object, enum: type[E], fallback: E) -> E:
    """Unknown stored values degrade to the default instead of propagating.

    The alternative -- raising -- would take a tenant's whole chatbot down over
    one bad row, and passing the raw value through would let that row choose
    the prompt text. Falling back is the only option that is both safe and
    survivable.
    """
    if isinstance(value, enum):
        return value
    try:
        return enum(str(value))
    except ValueError:
        return fallback


@dataclass(kw_only=True)
class TenantChatbotSettings(Entity):
    """Company-wide chatbot policy. One row per tenant."""

    tenant_id: UUID

    #: The master switch. False routes visitors straight to a human and must
    #: keep them out of RAG, the model, and both quotas entirely -- not merely
    #: hide the AI's replies.
    ai_chatbot_enabled: bool = True

    #: Chatbot-facing only. **Editing this must not rename the tenant**: the
    #: tenant's `display_name` is an account identity that appears in the
    #: console, in audit records and to platform operators, whereas this is
    #: what the bot calls the company when talking to a parent. They are
    #: usually the same string and are never the same field.
    company_name: str | None = None
    company_description: str = ""
    industry: str = DEFAULT_INDUSTRY

    allow_human_handoff: bool = True
    add_ai_summary_as_internal_comment: bool = False
    allow_ai_for_unassigned_conversations: bool = True

    #: The tenant's own daily cap. `None` => inherit the platform ceiling.
    #: Can only ever lower it -- see `TenantEntitlements.effective_daily_message_limit`.
    daily_message_limit: int | None = None

    share_visitor_location: bool = True

    #: How long a conversation is kept before automatic deletion.
    #:
    #: Not nullable, and there is no "keep forever" value. Widget
    #: conversations include anonymous visitors who never agreed to anything
    #: and cannot ask for their data back, so indefinite storage is a decision
    #: nobody made. A tenant that needs longer raises the number; the schema
    #: bounds it at ten years.
    conversation_retention_days: int = DEFAULT_RETENTION_DAYS

    created_at: datetime
    updated_at: datetime

    def resolved_company_name(self, tenant_display_name: str) -> str:
        """What the bot calls this company, in order of how deliberate it is.

        The tenant's own chatbot-facing name wins; failing that the account's
        display name, which someone did at least choose; and only if both are
        empty does the shipped default apply.
        """
        return (
            (self.company_name or "").strip()
            or (tenant_display_name or "").strip()
            or DEFAULT_COMPANY_NAME
        )

    def resolved_company_description(self, tenant_display_name: str) -> str:
        """The stored description, or the shipped one named for this nursery.

        Resolved on read rather than written into the row at creation, so the
        two stay distinguishable: a tenant who has never opened the Company tab
        still gets a coherent prompt, and the day they save their own text it
        replaces a default rather than an edit they never made.
        """
        stored = (self.company_description or "").strip()
        return stored or default_company_description(
            self.resolved_company_name(tenant_display_name)
        )


@dataclass(kw_only=True, frozen=True)
class AssistantBehaviour:
    """The assistant's brief. Read off `ai_assistants`, validated here."""

    role: str = DEFAULT_ROLE
    avoid: str = DEFAULT_AVOID
    personality: Personality = Personality.NEUTRAL
    response_length: ResponseLength = ResponseLength.BALANCED


@dataclass(kw_only=True, frozen=True)
class WidgetPresentation:
    """How one embedded widget introduces itself."""

    chatbot_name: str = DEFAULT_CHATBOT_NAME
    chatbot_title: str = DEFAULT_CHATBOT_TITLE
    avatar_key: str = DEFAULT_AVATAR_KEY
    greeting: str | None = None
    show_quick_reply_suggestions: bool = True

    def __post_init__(self) -> None:
        if self.avatar_key not in AVATAR_KEYS:
            # An allowlist, not a URL: see DEFAULT_AVATAR_KEY. Refusing an
            # unknown key here means the widget can render the value without
            # escaping it into an attribute that could carry an external
            # origin.
            raise ValueError(f"unknown avatar: {self.avatar_key}")
