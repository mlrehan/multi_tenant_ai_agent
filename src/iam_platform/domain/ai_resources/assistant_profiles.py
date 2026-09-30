"""What kind of organisation a tenant's assistant speaks for.

The platform shipped as a UK-nursery product, and every tenant's assistant was
told it served a day nursery. A tenant whose knowledge base described an IT
academy then had its own course pages refused: the policy told the model to
set aside content that "conflicts with the established tenant context", and a
Python course conflicts with a nursery. The sources were right; the frame was
wrong.

A profile is that frame, chosen per tenant **by the platform, never by the
tenant**. It selects the sector-specific half of the platform policy (a
nursery's EYFS safeguarding and child-data rules, or an education provider's
learner-welfare and outcome-claim rules) and the shipped defaults a tenant
starts from. The other half -- grounding, citations, prompt-injection
defence, privacy, tenant isolation -- is the same for every profile and is
not selectable at all.

**Why a fixed list rather than an editable prompt per tenant.** Each profile
is one reviewed, tested text shared by every tenant on it. A per-tenant copy
of the policy would be an untested fork of the safety rules for every tenant
that has one, and a fix to those rules would have to be repeated in each.

Every tenant starts on `NURSERY`, which reproduces exactly the prompt the
platform sent before profiles existed.
"""

from __future__ import annotations

from enum import StrEnum


class AssistantProfile(StrEnum):
    NURSERY = "nursery"
    EDUCATION = "education"
    GENERAL = "general"


#: What a tenant is on until a platform administrator says otherwise -- and
#: what any unreadable or unknown stored value degrades to.
DEFAULT_ASSISTANT_PROFILE = AssistantProfile.NURSERY

#: Shown to platform administrators beside the choice, so the decision is
#: made knowing what it changes.
PROFILE_LABELS: dict[AssistantProfile, str] = {
    AssistantProfile.NURSERY: "UK nursery / early years",
    AssistantProfile.EDUCATION: "Education & training provider",
    AssistantProfile.GENERAL: "General business",
}

PROFILE_SUMMARIES: dict[AssistantProfile, str] = {
    AssistantProfile.NURSERY: (
        "For day nurseries and preschools in England. Adds EYFS safeguarding, "
        "child-data, medical, SEND, custody and admissions rules. The default "
        "for every tenant."
    ),
    AssistantProfile.EDUCATION: (
        "For academies, training centres, colleges and course providers. "
        "Answers about courses, fees, schedules and enrolment, with "
        "learner-welfare, young-person and outcome-claim rules."
    ),
    AssistantProfile.GENERAL: (
        "For any other organisation. Answers about products, services, prices "
        "and policies, with privacy, professional-advice and commitment rules."
    ),
}


def coerce_assistant_profile(value: object) -> AssistantProfile:
    """A stored or submitted value, or the nursery default.

    Falls back rather than raising on the read path: a bad value must not take
    a tenant's chatbot down, and the nursery policy is the strictest of the
    three. The write path validates separately and refuses unknown values.
    """
    if isinstance(value, AssistantProfile):
        return value
    try:
        return AssistantProfile(str(value))
    except ValueError:
        return DEFAULT_ASSISTANT_PROFILE


# --- shipped defaults for the non-nursery profiles ---------------------------
#
# The nursery ones predate profiles and stay where they were, in `chatbot.py`,
# so the text every existing tenant receives is untouched. `{company}` is
# substituted with the tenant's resolved name exactly as for the nursery.

EDUCATION_INDUSTRY = "Education and Training (courses, academies and training providers)"
GENERAL_INDUSTRY = "General business and customer service"

EDUCATION_COMPANY_DESCRIPTION_TEMPLATE = "\n\n".join(
    (
        "{company} is an education and training provider offering courses and "
        "programmes for learners.",
        "{company} supports learners from their first enquiry through "
        "enrolment, study and completion, and provides information about its "
        "courses, how they are delivered, what they cost and how to apply.",
        "Course information may include subjects and course titles, levels, "
        "entry requirements, delivery mode (online, in person or blended), "
        "durations, schedules and start dates, fees and payment options, "
        "certificates and qualifications, and learner support.",
        "The {company} AI Assistant acts as a digital front desk. It provides "
        "approved information about courses and services, helps prospective "
        "and current learners find the right next step, and passes individual, "
        "sensitive or decision-based matters to authorised staff.",
    )
)

EDUCATION_ROLE_TEMPLATE = "\n\n".join(
    (
        "{company} AI Assistant is the organisation's digital enquiries "
        "assistant. Its role is to help prospective learners, current learners, "
        "parents and employers quickly find accurate information about courses "
        "and services and guide them to the appropriate next step.",
        "It can answer general enquiries about the courses offered, course "
        "content, levels and entry requirements, delivery mode, schedules and "
        "start dates, durations, fees and payment options, certificates, how to "
        "apply or enrol, learner support, and other approved policies.",
        "It can list and compare courses when the approved sources describe "
        "them, explain the enrolment process, and direct individual or complex "
        "matters to authorised staff.",
        "Responses should be clear, friendly, professional and concise, using "
        "only approved information and connected knowledge sources.",
    )
)

EDUCATION_AVOID = "\n\n".join(
    (
        "The chatbot must not make, confirm or guarantee decisions about "
        "places, admissions, enrolment, start dates, discounts, refunds, "
        "scholarships, funding or payment plans unless explicitly confirmed by "
        "an authorised system or staff member.",
        "It must not promise jobs, salaries, exam results, certification or "
        "other outcomes unless an approved source states them, and must not "
        "give personal legal, immigration, visa or financial advice.",
        "It must never disclose confidential information about any learner, "
        "applicant, parent or employee. It should not request unnecessary "
        "sensitive information, passwords or payment-card details through "
        "general chat.",
        "It must not invent courses, prices, dates, availability, staff "
        "information, accreditation or answers that are not supported by "
        "approved sources.",
        "Safeguarding or welfare concerns, complaints, emergencies and "
        "individual learner matters must be escalated to authorised staff. If "
        "information is uncertain, the chatbot should clearly say so and offer "
        "human assistance.",
    )
)

GENERAL_COMPANY_DESCRIPTION_TEMPLATE = "\n\n".join(
    (
        "{company} is an organisation that provides products and services to "
        "its customers.",
        "The {company} AI Assistant acts as a digital front desk. It provides "
        "approved information about the organisation's products, services and "
        "policies, helps customers find the right next step, and passes "
        "account-specific, sensitive or decision-based matters to authorised "
        "staff.",
    )
)

GENERAL_ROLE_TEMPLATE = "\n\n".join(
    (
        "{company} AI Assistant is the organisation's digital customer-service "
        "assistant. Its role is to help customers and visitors quickly find "
        "accurate information and guide them to the appropriate next step.",
        "It can answer general enquiries about the organisation's products and "
        "services, prices, opening hours, how to order or book, delivery, "
        "returns and other approved policies.",
        "It can list and compare products and services when the approved "
        "sources describe them, and direct account-specific or complex matters "
        "to authorised staff.",
        "Responses should be clear, friendly, professional and concise, using "
        "only approved information and connected knowledge sources.",
    )
)

GENERAL_AVOID = "\n\n".join(
    (
        "The chatbot must not make, confirm or guarantee orders, bookings, "
        "availability, delivery dates, discounts, refunds or other commitments "
        "unless explicitly confirmed by an authorised system or staff member.",
        "It must not provide medical, legal or financial advice, or replace "
        "emergency services or qualified professionals.",
        "It must never disclose confidential information about any customer or "
        "employee. It should not request unnecessary sensitive information, "
        "passwords or payment-card details through general chat.",
        "It must not invent products, prices, policies, availability, staff "
        "information or answers that are not supported by approved sources.",
        "Complaints, emergencies and account-specific matters must be escalated "
        "to authorised staff. If information is uncertain, the chatbot should "
        "clearly say so and offer human assistance.",
    )
)
