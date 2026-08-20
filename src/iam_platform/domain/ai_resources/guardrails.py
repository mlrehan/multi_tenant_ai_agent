# --------------------------------------------------------------
# src/iam_platform/domain/ai_resources/guardrails.py
# --------------------------------------------------------------

"""Enterprise input guardrails for the AI-resource domain.

This module is intentionally small, deterministic and provider-independent.

It protects the boundary between untrusted text and the model without pretending
that regex filtering is the primary security control. In this platform the
strongest protections remain structural and must stay enforced elsewhere:

* tenant and knowledge-base scope is resolved server-side;
* authenticated identity and permissions are never inferred from chat text;
* retrieved material is fenced and treated as reference content, not instruction;
* model answers are grounded in retrieved passages and citations are validated;
* state-changing operations require authorised application/tool paths;
* immutable platform policy outranks tenant-authored configuration and user text.

The two existing entry points deliberately have different semantics:

``screen_question``
    Handles visitor/user text. It normalises malformed Unicode, removes control
    characters, closes prompt-boundary escape attempts, enforces an input-size
    ceiling, and refuses only a narrow set of requests that are unambiguously
    inappropriate for a production multi-tenant assistant.

``neutralize_passage``
    Handles tenant-authored or retrieved reference material. It NEVER refuses
    the passage merely because it discusses an attack phrase. A nursery may
    legitimately index a safeguarding policy, security policy, privacy policy,
    complaint document or technical document containing phrases such as
    "ignore previous instructions". Refusing such passages would silently
    corrupt the tenant's knowledge base. Instead, structural delimiter escapes
    and unsafe control characters are neutralised.

Day-nursery / Early Years note
------------------------------
For a London/England nursery assistant, safeguarding, medical concerns, SEND,
custody/collection disputes, complaints and child-specific information are
high-risk subjects that normally require policy-aware handling or human
handoff. They are NOT automatically blocked by ``screen_question`` because a
parent must be able to report a safeguarding concern or ask for help.

To support enterprise routing without changing the behaviour or signatures of
the existing production functions, this module also exposes
``classify_nursery_risk``. It is a conservative, non-blocking signal only. It
does not make safeguarding, medical, legal, SEND or eligibility decisions, and
it must never be used as a substitute for the platform's system policy, staff
judgement, authentication, RBAC or emergency procedures.

Existing production function names and signatures are preserved.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

#: A user question, not a document. Very long pasted material degrades semantic
#: retrieval and can crowd out the authoritative context. Kept at the existing
#: production value to avoid an unexpected API behaviour change.
MAX_QUESTION_CHARS = 2000

#: A single retrieved passage is truncated rather than refused. This preserves
#: knowledge-base availability while preventing one pathological chunk from
#: consuming the model's context budget.
MAX_PASSAGE_CHARS = 8000

#: Delimiters used by the prompt/adapter layer. Domain code deliberately does
#: not import infrastructure, so the tokens are duplicated here and should be
#: protected by a test that keeps both sides in sync.
#:
#: ``>>>`` is included because it closes the current fence form. Defanging it
#: means a malicious or accidental copy of a complete fence cannot be recreated
#: from untrusted text.
FENCE_TOKENS = ("<<<SOURCE", "<<<END", ">>>")

#: Zero-width space inserted into a recognised fence token. It is intentionally
#: added *after* control-character stripping. If text is neutralised twice the
#: previous zero-width character is removed and then safely reinserted.
_FENCE_BREAK = "\u200b"


class GuardrailCategory(StrEnum):
    """Why a user question was refused.

    Values are suitable for security/audit telemetry. They intentionally
    describe a category rather than echoing the user's text, because the input
    may itself contain credentials or sensitive information.
    """

    EMPTY = "empty"
    TOO_LONG = "too_long"
    SYSTEM_PROMPT_EXTRACTION = "system_prompt_extraction"
    SECRET_EXTRACTION = "secret_extraction"
    CROSS_TENANT_PROBE = "cross_tenant_probe"
    AUTHORIZATION_BYPASS = "authorization_bypass"
    PROTECTED_DATA_EXTRACTION = "protected_data_extraction"


class NurseryRiskCategory(StrEnum):
    """Non-blocking Early Years risk signals for routing/telemetry.

    These categories MUST NOT be treated as findings or professional decisions.
    A match means only that the conversation may need stricter response policy,
    authenticated handling or human escalation.

    They are separate from :class:`GuardrailCategory` on purpose: parents must
    be able to report sensitive matters rather than being rejected by the
    security filter.
    """

    SAFEGUARDING = "safeguarding"
    IMMEDIATE_DANGER = "immediate_danger"
    MEDICAL_OR_MEDICATION = "medical_or_medication"
    SEND_OR_DEVELOPMENT = "send_or_development"
    CUSTODY_OR_COLLECTION = "custody_or_collection"
    CHILD_SPECIFIC_DATA = "child_specific_data"
    COMPLAINT_OR_ALLEGATION = "complaint_or_allegation"
    DATA_PROTECTION_OR_PRIVACY = "data_protection_or_privacy"
    PAYMENT_OR_FINANCIAL_CREDENTIALS = "payment_or_financial_credentials"


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    """The cleaned text, or the blocking reason(s).

    ``categories`` remains the existing blocking contract. A question is
    allowed only when it contains no blocking category.

    The class deliberately does not include nursery risk signals so existing
    equality, serialisation and call-site behaviour remain stable. Use
    :func:`classify_nursery_risk` separately where the application supports
    risk-aware routing.
    """

    text: str
    categories: tuple[GuardrailCategory, ...] = field(default=())

    @property
    def allowed(self) -> bool:
        return not self.categories


@dataclass(frozen=True, slots=True)
class NurseryRiskAssessment:
    """A non-blocking classification result for an Early Years conversation."""

    categories: tuple[NurseryRiskCategory, ...] = field(default=())

    @property
    def has_risk(self) -> bool:
        return bool(self.categories)

    @property
    def requires_priority_review(self) -> bool:
        """Whether deterministic signals indicate urgent/high-risk handling.

        This is a routing hint only. It does not decide whether a statutory
        safeguarding threshold, medical emergency threshold or referral
        threshold has been met.
        """

        priority = {
            NurseryRiskCategory.SAFEGUARDING,
            NurseryRiskCategory.IMMEDIATE_DANGER,
            NurseryRiskCategory.CUSTODY_OR_COLLECTION,
            NurseryRiskCategory.COMPLAINT_OR_ALLEGATION,
        }
        return any(category in priority for category in self.categories)


# ---------------------------------------------------------------------------
# Blocking rules
# ---------------------------------------------------------------------------
#
# These patterns are deliberately NARROW.
#
# The correct question is not "could this wording be malicious?" Almost any
# sentence could be part of a prompt attack. The correct question is "does this
# wording represent a request that the production assistant should never
# fulfil, with little realistic risk of refusing a legitimate nursery enquiry?"
#
# Structural protections carry the real security boundary. These rules provide
# defence-in-depth and high-signal security events.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_EXTRACTION = re.compile(
    r"\b(?:"
    r"(?:reveal|show|print|repeat|output|display|disclose|dump|expose)\s+"
    r"(?:me\s+)?(?:your|the)\s+"
    r"(?:system\s+prompt|developer\s+prompt|hidden\s+prompt|"
    r"initial\s+instructions?|original\s+instructions?|hidden\s+instructions?|"
    r"internal\s+instructions?|system\s+instructions?|guardrail(?:s)?|"
    r"policy\s+prompt)"
    r"|what\s+(?:is|are)\s+your\s+"
    r"(?:system\s+prompt|developer\s+prompt|hidden\s+prompt|"
    r"initial\s+instructions?|hidden\s+instructions?|internal\s+instructions?)"
    r")\b",
    re.IGNORECASE,
)

_SECRET_EXTRACTION = re.compile(
    r"(?:\b(?:your|the)\s+(?:(?:openai|anthropic|gemini|google|azure|aws|"
    r"database|postgres(?:ql)?|redis|qdrant)\s+)?"
    r"(?:api[\s_-]?key|secret[\s_-]?key|client[\s_-]?secret|"
    r"access[\s_-]?token|refresh[\s_-]?token|bearer[\s_-]?token|"
    r"private[\s_-]?key|credential(?:s)?|password|connection\s+string|"
    r"database[\s_-]?url)\b)"
    r"|(?:\benv(?:ironment)?\s+variables?\b.{0,80}\b"
    r"(?:print|show|list|dump|reveal|display|output)\b)"
    r"|(?:\b(?:print|show|list|dump|reveal|display|output)\b.{0,80}\b"
    r"env(?:ironment)?\s+variables?\b)",
    re.IGNORECASE | re.DOTALL,
)

# Existing tenant-probe semantics are preserved and extended to cover common
# nursery/provider wording. Server-side tenant isolation is still authoritative.
_CROSS_TENANT_PROBE = re.compile(
    r"(?:\b(?:other|another|different|all)\s+"
    r"(?:tenants?|organi[sz]ations?|customers?|companies|nurseries|providers)\b)"
    r"|(?:\btenant[\s_-]?id\s*[=:])"
    r"|(?:\b(?:documents?|data|records?|conversations?|files?)\s+"
    r"(?:of|from|belonging\s+to)\s+"
    r"(?:other|another|a\s+different)\s+"
    r"(?:tenant|organi[sz]ation|customer|company|nursery|provider)\b)",
    re.IGNORECASE,
)

_AUTHORIZATION_BYPASS = re.compile(
    r"\b(?:"
    r"(?:bypass|circumvent|disable|defeat|ignore|skip)\s+"
    r"(?:authentication|authorisation|authorization|rbac|permissions?|"
    r"access\s+controls?|tenant\s+isolation|row[\s_-]?level\s+security|rls)"
    r"|(?:act|pretend)\s+as\s+(?:an?\s+)?(?:admin|administrator|staff|manager)\s+"
    r"(?:without|even\s+without)\s+(?:logging\s+in|authentication|permission)"
    r")\b",
    re.IGNORECASE,
)

# This rule is intentionally limited to requests for another person's/group's
# protected records. It does NOT block "show me my child's record", because an
# authenticated product may legitimately support that through server-side
# authorisation. The model itself still must not expose records merely because
# someone claims a relationship in chat.
_PROTECTED_DATA_EXTRACTION = re.compile(
    r"\b(?:show|list|give|send|export|download|reveal|display|provide)\s+"
    r"(?:me\s+)?"
    r"(?:all\s+|another\s+|other\s+|someone\s+else'?s\s+)"
    r"(?:children'?s?|child(?:ren)?|parents?|famil(?:y|ies)|guardians?|staff)\s+"
    r"(?:records?|data|details?|contact\s+details?|medical\s+records?|"
    r"health\s+records?|attendance|addresses?|phone\s+numbers?|emails?)\b",
    re.IGNORECASE,
)

_RULES: tuple[tuple[GuardrailCategory, re.Pattern[str]], ...] = (
    (GuardrailCategory.SYSTEM_PROMPT_EXTRACTION, _SYSTEM_PROMPT_EXTRACTION),
    (GuardrailCategory.SECRET_EXTRACTION, _SECRET_EXTRACTION),
    (GuardrailCategory.CROSS_TENANT_PROBE, _CROSS_TENANT_PROBE),
    (GuardrailCategory.AUTHORIZATION_BYPASS, _AUTHORIZATION_BYPASS),
    (GuardrailCategory.PROTECTED_DATA_EXTRACTION, _PROTECTED_DATA_EXTRACTION),
)


# ---------------------------------------------------------------------------
# Non-blocking nursery / Early Years risk signals
# ---------------------------------------------------------------------------
#
# These patterns only classify. They never refuse a question. They intentionally
# favour recall over precision because their safe downstream action is stricter
# handling or human review, not denial.
# ---------------------------------------------------------------------------

_SAFEGUARDING_RISK = re.compile(
    r"\b(?:"
    r"abuse|abused|abusive|neglect|neglected|maltreat(?:ment|ed)?|"
    r"sexual\s+abuse|molest(?:ed|ation)?|groom(?:ing|ed)?|exploitation|"
    r"domestic\s+abuse|domestic\s+violence|coercive\s+control|"
    r"physical\s+harm|emotional\s+abuse|unexplained\s+injur(?:y|ies)|"
    r"hit\s+(?:my|a|the)\s+child|hurt\s+(?:my|a|the)\s+child|"
    r"staff\s+(?:hit|hurt|smack(?:ed)?|shout(?:ed)?\s+at)\s+"
    r"(?:my|a|the)\s+child|"
    r"missing\s+child|child\s+(?:is\s+)?missing|left\s+alone|abandon(?:ed|ment)?|"
    r"radicali[sz](?:e|ed|ation)|female\s+genital\s+mutilation|fgm|"
    r"honour[\s-]+based\s+abuse|forced\s+marriage"
    r")\b",
    re.IGNORECASE,
)

_IMMEDIATE_DANGER_RISK = re.compile(
    r"\b(?:"
    r"not\s+breathing|cannot\s+breathe|can't\s+breathe|difficulty\s+breathing|"
    r"choking|unconscious|unresponsive|seizure|anaphylaxis|"
    r"severe\s+allergic\s+reaction|heavy\s+bleeding|"
    r"life[\s-]+threatening|immediate\s+danger|"
    r"threat(?:en(?:ed|ing)?)?\s+to\s+(?:kill|harm)|"
    r"fire\s+(?:at|in)\s+(?:the\s+)?nursery"
    r")\b",
    re.IGNORECASE,
)

_MEDICAL_OR_MEDICATION_RISK = re.compile(
    r"\b(?:"
    r"medicine|medication|dose|dosage|prescription|antibiotic|"
    r"inhaler|epipen|epi[\s-]?pen|adrenaline|allerg(?:y|ic)|"
    r"asthma|seizure|fever|temperature|vomit(?:ing)?|diarrhoea|"
    r"injur(?:y|ed)|accident|first\s+aid|medical"
    r")\b",
    re.IGNORECASE,
)

_SEND_OR_DEVELOPMENT_RISK = re.compile(
    r"\b(?:"
    r"send|senco|sen\b|special\s+educational\s+needs?|"
    r"autis(?:m|tic)|adhd|developmental\s+delay|development\s+concern|"
    r"speech\s+delay|language\s+delay|ehcp|education,\s*health\s+and\s+care\s+plan|"
    r"additional\s+needs?|one[\s-]+to[\s-]+one\s+support"
    r")\b",
    re.IGNORECASE,
)

_CUSTODY_OR_COLLECTION_RISK = re.compile(
    r"\b(?:"
    r"custody|parental\s+responsibility|court\s+order|prohibited\s+steps\s+order|"
    r"child\s+arrangements?\s+order|authorised\s+collection|authorized\s+collection|"
    r"unauthorised\s+collection|unauthorized\s+collection|"
    r"not\s+allowed\s+to\s+(?:collect|pick\s+up)|"
    r"(?:collect|pick\s+up)\s+(?:my|the)\s+child"
    r")\b",
    re.IGNORECASE,
)

_CHILD_SPECIFIC_DATA_RISK = re.compile(
    r"\b(?:"
    r"my\s+child|my\s+son|my\s+daughter|"
    r"child'?s\s+(?:attendance|record|records|observation|observations|"
    r"learning\s+journal|incident|accident|health|medical|development|progress)|"
    r"key\s+person\s+notes?|daily\s+diary"
    r")\b",
    re.IGNORECASE,
)

_COMPLAINT_OR_ALLEGATION_RISK = re.compile(
    r"\b(?:"
    r"complaint|complain|formal\s+complaint|allegation|allege(?:d)?|"
    r"report\s+(?:a\s+)?staff|report\s+(?:the\s+)?nursery|"
    r"staff\s+conduct|misconduct|whistleblow(?:ing|er)?"
    r")\b",
    re.IGNORECASE,
)

_DATA_PROTECTION_OR_PRIVACY_RISK = re.compile(
    r"\b(?:"
    r"subject\s+access\s+request|sar\b|data\s+protection|uk\s+gdpr|gdpr|"
    r"privacy\s+request|delete\s+my\s+data|erase\s+my\s+data|"
    r"right\s+to\s+erasure|data\s+breach|privacy\s+breach|"
    r"personal\s+data|special\s+category\s+data"
    r")\b",
    re.IGNORECASE,
)

_PAYMENT_OR_FINANCIAL_CREDENTIALS_RISK = re.compile(
    r"\b(?:"
    r"card\s+number|credit\s+card|debit\s+card|cvv|cvc|pin\s+number|"
    r"bank\s+password|online\s+banking|bank\s+login|account\s+password"
    r")\b",
    re.IGNORECASE,
)

_NURSERY_RISK_RULES: tuple[tuple[NurseryRiskCategory, re.Pattern[str]], ...] = (
    (NurseryRiskCategory.SAFEGUARDING, _SAFEGUARDING_RISK),
    (NurseryRiskCategory.IMMEDIATE_DANGER, _IMMEDIATE_DANGER_RISK),
    (NurseryRiskCategory.MEDICAL_OR_MEDICATION, _MEDICAL_OR_MEDICATION_RISK),
    (NurseryRiskCategory.SEND_OR_DEVELOPMENT, _SEND_OR_DEVELOPMENT_RISK),
    (NurseryRiskCategory.CUSTODY_OR_COLLECTION, _CUSTODY_OR_COLLECTION_RISK),
    (NurseryRiskCategory.CHILD_SPECIFIC_DATA, _CHILD_SPECIFIC_DATA_RISK),
    (NurseryRiskCategory.COMPLAINT_OR_ALLEGATION, _COMPLAINT_OR_ALLEGATION_RISK),
    (NurseryRiskCategory.DATA_PROTECTION_OR_PRIVACY, _DATA_PROTECTION_OR_PRIVACY_RISK),
    (
        NurseryRiskCategory.PAYMENT_OR_FINANCIAL_CREDENTIALS,
        _PAYMENT_OR_FINANCIAL_CREDENTIALS_RISK,
    ),
)


def _strip_control_characters(text: str) -> str:
    """Normalise and remove characters that can alter interpretation.

    The function preserves real user formatting while removing characters that
    have no legitimate need at this model boundary.

    Processing order matters:

    1. normalise common line separators to ``\\n``;
    2. apply Unicode NFKC so full-width/styled variants collapse to their
       canonical form before security patterns run;
    3. remove Unicode ``Cc``/``Cf`` controls except tab/newline.

    This strips NULs, terminal/ANSI controls and Unicode bidi/format controls
    commonly involved in visual-spoofing / "Trojan Source" style attacks.

    Existing function name and signature are intentionally preserved.
    """

    # Preserve semantic line breaks before category-based stripping.
    text = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
    )

    normalized = unicodedata.normalize("NFKC", text)

    return "".join(
        ch
        for ch in normalized
        if ch in "\t\n" or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def _neutralize_fence_tokens(text: str) -> str:
    """Defang model-context delimiters without removing the surrounding text.

    Matching is case-insensitive so a future adapter change to case-insensitive
    parsing cannot silently reopen the boundary. The visible content remains
    effectively unchanged for humans and for semantic retrieval.
    """

    cleaned = text
    for token in FENCE_TOKENS:
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        cleaned = pattern.sub(
            lambda match: match.group(0)[0] + _FENCE_BREAK + match.group(0)[1:],
            cleaned,
        )
    return cleaned


def screen_question(question: str) -> GuardrailVerdict:
    """Clean and screen user-supplied question text.

    This preserves the existing production contract: return a
    :class:`GuardrailVerdict` rather than raising, so authenticated and anonymous
    entry points can decide how to record/report refusal.

    The function refuses only:

    * empty input;
    * oversized input;
    * explicit extraction of hidden prompts/instructions;
    * explicit extraction of credentials/secrets;
    * explicit cross-tenant probing;
    * explicit attempts to bypass authentication/authorisation boundaries;
    * explicit bulk/other-person protected-record extraction.

    Safeguarding, medical, SEND, custody, complaint and other sensitive nursery
    topics are NOT blocked here. A parent or carer must be able to report them.
    Use :func:`classify_nursery_risk` for non-blocking routing signals.

    Existing function name and signature are intentionally preserved.
    """

    cleaned = _strip_control_characters(question).strip()

    if not cleaned:
        return GuardrailVerdict(
            text="",
            categories=(GuardrailCategory.EMPTY,),
        )

    # Enforce the size limit before fence neutralisation, which may add a small
    # number of zero-width break characters.
    if len(cleaned) > MAX_QUESTION_CHARS:
        return GuardrailVerdict(
            text=cleaned,
            categories=(GuardrailCategory.TOO_LONG,),
        )

    # A current question may later be persisted into conversation memory.
    # Defanging fence tokens now therefore also protects future HISTORY blocks.
    cleaned = _neutralize_fence_tokens(cleaned)

    matched = tuple(
        category
        for category, pattern in _RULES
        if pattern.search(cleaned)
    )

    return GuardrailVerdict(
        text=cleaned,
        categories=matched,
    )


def classify_nursery_risk(text: str) -> NurseryRiskAssessment:
    """Return conservative, NON-BLOCKING Early Years risk signals.

    This helper is intentionally deterministic and provider-independent. It can
    be used by application services for routing, audit metadata or deciding that
    stricter policy/handoff handling should be considered.

    It must NOT be used to:

    * decide whether abuse occurred;
    * determine a safeguarding referral threshold;
    * diagnose or assess a medical condition;
    * determine SEND/EHCP eligibility;
    * determine parental responsibility/custody;
    * authorise access to a child's record;
    * replace a Designated Safeguarding Lead, SENCO, healthcare professional,
      local authority, police or emergency service.

    A category match is only a signal that the conversation may need elevated
    care. Authentication, tenant isolation, RBAC and human escalation remain
    application responsibilities.
    """

    cleaned = _strip_control_characters(text).strip()
    if not cleaned:
        return NurseryRiskAssessment()

    categories = tuple(
        category
        for category, pattern in _NURSERY_RISK_RULES
        if pattern.search(cleaned)
    )
    return NurseryRiskAssessment(categories=categories)


def neutralize_passage(text: str) -> str:
    """Make retrieved/tenant-authored reference text safe for fenced context.

    This function NEVER refuses a passage.

    That distinction is essential for an enterprise nursery knowledge base:
    safeguarding policies, privacy policies, security procedures and complaint
    documentation can legitimately contain language that resembles an attack.
    Dropping those passages would make the knowledge base incomplete and could
    make the assistant less safe.

    The function therefore performs structural neutralisation only:

    * Unicode/control-character sanitisation;
    * prompt-fence escape neutralisation;
    * deterministic per-passage length bounding.

    The passage remains reference material. It does not gain instruction
    authority merely because it was uploaded, crawled or retrieved.

    Existing function name and signature are intentionally preserved.
    """

    cleaned = _strip_control_characters(text)
    cleaned = _neutralize_fence_tokens(cleaned)

    if len(cleaned) > MAX_PASSAGE_CHARS:
        cleaned = cleaned[:MAX_PASSAGE_CHARS] + "\n[passage truncated]"

    return cleaned


__all__ = [
    "FENCE_TOKENS",
    "GuardrailCategory",
    "GuardrailVerdict",
    "MAX_PASSAGE_CHARS",
    "MAX_QUESTION_CHARS",
    "NurseryRiskAssessment",
    "NurseryRiskCategory",
    "classify_nursery_risk",
    "neutralize_passage",
    "screen_question",
]
