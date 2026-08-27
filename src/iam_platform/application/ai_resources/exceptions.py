from __future__ import annotations


class AiResourceError(Exception):
    pass


class AssistantNotFoundError(AiResourceError):
    pass


class KnowledgeBaseNotFoundError(AiResourceError):
    pass


class DocumentNotFoundError(AiResourceError):
    pass


class DataSourceNotFoundError(AiResourceError):
    pass


class ModelConfigurationManagementDeniedError(AiResourceError):
    """The caller may not govern the model-configuration catalogue.

    A 403 rather than a 404: the catalogue is platform infrastructure, not a
    tenant-owned resource, so there is no cross-tenant existence to conceal --
    and telling an operator "you lack this permission" is the useful answer.
    Contrast `ModelConfigurationNotFoundError`, which a *tenant* gets for a
    configuration they were never granted, precisely so they cannot tell an
    unavailable one from a non-existent one.
    """

    def __init__(self, required_permission: str) -> None:
        super().__init__(f"missing required permission: {required_permission}")
        self.required_permission = required_permission


class ModelConfigurationInUseError(AiResourceError):
    """Revoking would strand assistants that still use this configuration."""


class DailyMessageLimitExceededError(AiResourceError):
    """The tenant has used its whole daily AI-message allowance.

    A 429, like the token budget it sits beside: the caller's permissions are
    fine and the same request succeeds tomorrow. A 403 would send them looking
    for a permission problem that does not exist.

    Distinct from `WidgetQuotaExceededError`, which is the *public* surface's
    deliberately opaque refusal -- this one answers an authenticated tenant
    admin who is entitled to know exactly which limit they hit and when it
    resets.
    """


class TokenBudgetExceededError(AiResourceError):
    """This month's token budget for the chosen model is spent.

    A 429, not a 403: the caller's permissions are fine and the same request
    will succeed next month or once the platform raises the budget. Telling
    them "forbidden" would send them looking for a permission problem that
    does not exist.

    Also raised when the counter cannot be *read*, deliberately: an
    unconfirmable budget must not silently become an unlimited one, and the
    tenant sees the same "try later" either way, which is true in both cases.
    """


class ConversationNotFoundError(AiResourceError):
    pass


class ModelConfigurationNotFoundError(AiResourceError):
    pass


class ProviderCredentialNotFoundError(AiResourceError):
    pass


class ProviderCredentialUnusableError(AiResourceError):
    """A model configuration names a provider credential that cannot be used --
    revoked, missing, undecryptable, or rejected by the provider.

    **Raised rather than falling back to the platform's own key**, which is the
    entire point. A silent fallback would keep answering while quietly moving
    the bill from the tenant's provider account to the platform's, and nothing
    in the response would say so. Failing loudly is the only version of this
    an operator can notice.

    A 409, not a 404 or 403: the request is well-formed and the caller is
    entitled to make it; the *configuration* is in a state that prevents it,
    and someone with access to the console can fix it.
    """


class DocumentContentNotFoundError(AiResourceError):
    """A ``documents`` row exists but its bytes are missing from object storage.

    Distinct from ``DocumentNotFoundError``, which means "no such row, or you
    can't see it". This one is an *internal inconsistency*: the database says
    the content is there and storage disagrees. It maps to 500, not 404,
    because 404 would tell the caller their document doesn't exist when in
    fact the platform lost it -- a materially different thing to be told, and
    one that should page an operator rather than be quietly absorbed.
    """


class DocumentParseError(AiResourceError):
    """A document's bytes could not be read -- corrupt, encrypted, or not the
    format its content type declared.

    A tenant-fixable problem, not a platform fault: the message is recorded on
    ``documents.failure_reason`` and shown in the console so the person who
    uploaded a password-protected PDF learns that, instead of seeing an opaque
    red badge and opening a support ticket.
    """


class DocumentTooLargeError(AiResourceError):
    """The upload exceeds the per-file size cap.

    Raised while *reading* the body, not after -- materialising an arbitrarily
    large upload before deciding to reject it is the denial-of-service the cap
    exists to prevent.
    """


class UnsupportedDocumentTypeError(AiResourceError):
    """No parser handles this content type.

    Distinct from ``DocumentParseError``: the file may be perfectly valid, the
    platform simply cannot read that format. Different message, different fix.
    """


class PermissionDeniedError(AiResourceError):
    def __init__(self, required_permission: str) -> None:
        super().__init__(f"missing required permission: {required_permission}")
        self.required_permission = required_permission


class ResourceAccessDeniedError(AiResourceError):
    """The caller may not see or modify this specific resource, even though
    they hold the generic tenant permission for its type -- the per-resource
    visibility/ownership check in ``domain.ai_resources.policies`` said no.

    Raised (rather than returning a 404-shaped "not found") only where the
    caller has already proven they can see the resource; discovery-time
    denials surface as *NotFound* instead, so a caller can never use the error
    shape to infer that a resource they can't see exists.
    """


class TooManyUrlsError(AiResourceError):
    """More URLs in one data source than this platform will accept."""


class UnsafeCrawlTargetError(AiResourceError):
    """A URL this platform refuses to fetch -- see infrastructure/crawling/url_safety.py.

    Declared here, deriving from `AiResourceError`, rather than left as the
    infrastructure module's own `ValueError`. A bare `ValueError` reaching the
    API surfaces as an unhandled 500, which is both wrong (the tenant supplied
    something invalid -- that is a 400) and unhelpful (it hides the one message
    that would tell them what to change). This project has shipped that exact
    bug three times; `tests/unit/test_exception_mapping_is_exhaustive.py`
    exists because of it, and it can only see exceptions rooted in a module
    base class like this one.
    """


class QuestionBlockedError(AiResourceError):
    """The guardrail layer refused this question.

    A 400, not a 403: the caller's permissions are irrelevant -- the *content*
    of the request is what was refused, and no amount of additional access
    would change the answer. The message says what was refused without echoing
    the question back, which could itself carry an injected instruction into a
    log or an error page.
    """


class QuestionTooLongError(AiResourceError):
    """An empty question, or one long enough to be a paste rather than a query.

    Under `AiResourceError` so the exhaustive-mapping guard can see it -- a
    bare `ValueError` here would surface as a 500 (see the Phase 12 note in
    docs/24).
    """


class WidgetUnavailableError(AiResourceError):
    """No such widget, or it is disabled, or the session no longer matches it.

    One error for several causes on purpose: telling an anonymous caller
    whether a guessed public key exists turns this endpoint into a probing
    oracle.
    """


class WidgetOriginNotAllowedError(AiResourceError):
    """The requesting site is not on this widget's allowlist."""


class WidgetQuotaExceededError(AiResourceError):
    """This widget has used its daily question allowance."""


class ChatWidgetNotFoundError(AiResourceError):
    """No widget with that id in this tenant.

    Distinct from `WidgetUnavailableError`: this one answers an *authenticated*
    tenant admin who already holds the manage permission, so a 404 tells them
    only about their own tenant and is no oracle. The anonymous surface keeps
    its single opaque error.
    """


class ChatWidgetInvalidError(AiResourceError):
    """The submitted widget settings cannot be stored as given.

    A 400: the caller may do this, they have simply sent something unusable --
    an empty name, or an origin list with nothing resolvable in it. Named
    rather than reusing a bare `ValueError`, which no handler maps and which
    this codebase has seen surface as a 500 three separate times.
    """


class ChatWidgetInUseError(AiResourceError):
    """The widget has conversations and cannot be deleted without losing them.

    A 409: nothing about the request is malformed and the caller's permissions
    are fine -- the resource is simply in a state that refuses this operation.
    The message names the alternative (disable), because a refusal that does
    not say what to do instead is a dead end.
    """


class EntitlementExceededError(AiResourceError):
    """The tenant is at the platform's ceiling for this resource.

    A 409, not a 403: the caller's permissions are fine and the request is
    well-formed -- the account has simply reached a limit, and the fix is
    either deleting something or asking the platform to raise it. Saying
    "forbidden" would send a tenant admin hunting for a permission problem
    that does not exist.

    The message names the number deliberately. "Limit reached" with no figure
    leaves the tenant unable to tell whether they are one over or a hundred.
    """

    def __init__(self, *, resource: str, limit: int, current: int) -> None:
        super().__init__(
            f"this tenant's plan allows {limit} {resource} and {current} are in use; "
            "remove one or ask your platform administrator to raise the limit"
        )
        self.resource = resource
        self.limit = limit
        self.current = current


class FeatureNotEntitledError(AiResourceError):
    """The platform has not enabled this capability for this tenant.

    A 403: unlike a quota, this is not something the tenant can resolve by
    tidying up. Naming the capability is safe -- it is the tenant's own plan,
    not another tenant's data -- and is the only way the admin knows what to
    ask their platform contact for.
    """

    def __init__(self, capability: str) -> None:
        readable = capability.removeprefix("allow_").replace("_", " ")
        super().__init__(
            f"this tenant's plan does not include: {readable}. "
            "Ask your platform administrator to enable it."
        )
        self.capability = capability


class DailyMessageQuotaExceededError(AiResourceError):
    """Today's AI message allowance is spent.

    A 429: the same request succeeds tomorrow, or once the limit is raised.
    Also raised when the counter cannot be *read* -- an unconfirmable quota
    must not silently become an unlimited one, and "try again shortly" is true
    in both cases.
    """


class TenantTokenQuotaExceededError(AiResourceError):
    """This calendar month's token allowance is spent.

    Distinct from `TokenBudgetExceededError`, which bounds one *model
    configuration*. This one bounds the whole tenant, so a tenant with three
    granted models cannot spend three budgets' worth. Both are 429s and either
    stopping is enough.
    """


class ChatbotDisabledError(AiResourceError):
    """The tenant has turned the AI off; this conversation is human-only.

    Not an error the visitor should ever see as a failure -- the widget routes
    them to a human instead. It exists so an AI entry point called while the
    chatbot is disabled fails loudly rather than quietly answering, which is
    the outcome that would consume quota the tenant switched off.
    """


class HandoffNotAvailableError(AiResourceError):
    """Handoff was requested but the tenant has not enabled or configured it."""


class ConversationAlreadyClaimedError(AiResourceError):
    """Another agent claimed this conversation first.

    A 409. The losing side of a genuine race, reported rather than hidden:
    silently succeeding would show two agents the same conversation as theirs
    and let them both start typing.
    """


class TeamNotFoundError(AiResourceError):
    pass


class ChatbotSettingsInvalidError(AiResourceError):
    """A chatbot setting was rejected -- most usefully, a daily limit above
    the platform ceiling.

    **Derives from `AiResourceError`, and that is the point.** The first
    version of this raised a bare `ValueError`, which no handler in
    `api/exception_handlers.py` matches, so a tenant admin typing 5,000
    against a ceiling of 1,000 got a 500 and no explanation. The exhaustiveness
    guard could not catch it either, because it only scans `AiResourceError`
    subclasses -- the third time this codebase has learned that an exception
    declared outside the module's base class is invisible to the very test
    written to prevent this.

    A 400: the request is understood and refused for a reason the caller can
    act on, and the message names the ceiling so they know what to ask for.
    """


class PushSubscriptionInvalidError(AiResourceError):
    """The browser sent something that is not a usable push subscription.

    Under `AiResourceError` rather than a bare exception beside its use case --
    the fourth time this file has had to absorb that lesson. An exception
    declared elsewhere is invisible both to `api/exception_handlers.py` and to
    `test_exception_mapping_is_exhaustive.py`, which only scans subclasses of
    this base, so it would surface as a 500 that no guard could see coming.

    A 400: the endpoint or its keys are malformed, which is a fact about the
    request.
    """
