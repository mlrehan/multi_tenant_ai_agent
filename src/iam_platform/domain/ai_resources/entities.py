"""AI-resource domain entities -- see docs/16-schema-ai-resources.md.

``integrations`` and ``data_sources`` are intentionally not modeled yet -- see
the Phase 7 scope note (CLAUDE.md): external-system sync is an ops
integration with no bearing on the authorization model this phase exists to
prove. Message-level conversation content is likewise deferred; docs/16 left
its storage shape to be decided here, and the decision is that it belongs
with AI serving, not with authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from iam_platform.domain.ai_resources.chatbot import (
    MAX_DIRECT_TEXT_CHARS,
    Personality,
    ResponseLength,
)
from iam_platform.domain.shared.entity import Entity
from iam_platform.domain.shared.exceptions import InvalidStateTransitionError


class ResourceVisibility(StrEnum):
    TENANT = "tenant"
    DEPARTMENT = "department"
    TEAM = "team"
    RESTRICTED = "restricted"


class AssistantStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


@dataclass(kw_only=True)
class AiAssistant(Entity):
    tenant_id: UUID
    name: str
    description: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.TENANT
    department_id: UUID | None = None
    team_id: UUID | None = None
    owner_membership_id: UUID
    model_configuration_id: UUID
    status: AssistantStatus = AssistantStatus.DRAFT
    system_prompt: str | None = None

    #: The guided brief the chatbot console edits. `system_prompt` is kept as
    #: the free-form escape hatch and both are appended to the platform's own
    #: rules, never substituted for them -- these are tenant input, and the
    #: grounding rules are the guarantee this platform advertises.
    role_instructions: str | None = None
    avoid_instructions: str | None = None
    #: Enum-backed. The stored label never reaches the model; it selects one of
    #: four fixed instruction strings, so this is not an injection surface even
    #: if a row is written by hand.
    personality: Personality = Personality.NEUTRAL
    response_length: ResponseLength = ResponseLength.BALANCED
    created_at: datetime
    updated_at: datetime

    def publish(self, *, now: datetime) -> None:
        if self.status == AssistantStatus.ARCHIVED:
            raise InvalidStateTransitionError("cannot publish an archived assistant")
        self.status = AssistantStatus.PUBLISHED
        self.updated_at = now

    def unpublish(self, *, now: datetime) -> None:
        if self.status != AssistantStatus.PUBLISHED:
            raise InvalidStateTransitionError(f"cannot unpublish from status {self.status}")
        self.status = AssistantStatus.DRAFT
        self.updated_at = now

    def archive(self, *, now: datetime) -> None:
        if self.status == AssistantStatus.ARCHIVED:
            raise InvalidStateTransitionError("assistant already archived")
        self.status = AssistantStatus.ARCHIVED
        self.updated_at = now

    def update_details(
        self,
        *,
        name: str,
        description: str | None,
        system_prompt: str | None,
        model_configuration_id: UUID,
        now: datetime,
    ) -> None:
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        self.model_configuration_id = model_configuration_id
        self.updated_at = now

    def change_visibility(
        self,
        *,
        visibility: ResourceVisibility,
        department_id: UUID | None,
        team_id: UUID | None,
        now: datetime,
    ) -> None:
        # A department/team-scoped resource without its scoping column set
        # would silently fall back to "nobody matches" in the visibility
        # policy -- reject it here so the invalid state can never be stored.
        if visibility == ResourceVisibility.DEPARTMENT and department_id is None:
            raise InvalidStateTransitionError("department visibility requires a department_id")
        if visibility == ResourceVisibility.TEAM and team_id is None:
            raise InvalidStateTransitionError("team visibility requires a team_id")
        self.visibility = visibility
        self.department_id = department_id
        self.team_id = team_id
        self.updated_at = now


class AssistantAccessLevel(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"


@dataclass(kw_only=True)
class AssistantMember(Entity):
    tenant_id: UUID
    assistant_id: UUID
    membership_id: UUID
    access_level: AssistantAccessLevel = AssistantAccessLevel.VIEWER
    added_at: datetime


@dataclass(kw_only=True)
class KnowledgeBase(Entity):
    tenant_id: UUID
    name: str
    description: str | None = None
    owner_membership_id: UUID
    visibility: ResourceVisibility = ResourceVisibility.TENANT
    department_id: UUID | None = None
    team_id: UUID | None = None
    # Server-generated as `{tenant_id}/{id}`, never accepted as client input
    # (docs/16-schema-ai-resources.md) -- the concrete mechanism behind the
    # mandatory server-injected tenant filter on every vector query.
    vector_namespace: str
    created_at: datetime
    updated_at: datetime


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass(kw_only=True)
class Document(Entity):
    tenant_id: UUID
    knowledge_base_id: UUID
    uploaded_by_membership_id: UUID
    filename: str
    content_type: str
    # Server-generated as `{tenant_id}/{knowledge_base_id}/{document_id}`,
    # same never-client-suppliable rule as `vector_namespace`.
    storage_path: str
    size_bytes: int
    status: DocumentStatus = DocumentStatus.PROCESSING
    checksum: str
    created_at: datetime
    deleted_at: datetime | None = None
    #: Why ingestion failed. Set only alongside FAILED, and cleared on a
    #: successful re-ingest so a stale reason can't outlive the failure.
    failure_reason: str | None = None
    #: The page this document was crawled from, or None for an uploaded file.
    #: Written by the crawl worker; carried on the entity so a reader can tell
    #: an uploaded document from a crawled one without joining `data_sources`.
    source_url: str | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def mark_ready(self) -> None:
        if self.status != DocumentStatus.PROCESSING:
            raise InvalidStateTransitionError(f"cannot mark ready from status {self.status}")
        self.status = DocumentStatus.READY
        self.failure_reason = None

    def mark_failed(self, *, reason: str | None = None) -> None:
        if self.status != DocumentStatus.PROCESSING:
            raise InvalidStateTransitionError(f"cannot mark failed from status {self.status}")
        self.status = DocumentStatus.FAILED
        self.failure_reason = reason

    def mark_processing(self) -> None:
        """Puts a document back in the queue's hands, for a retry.

        The reverse of `mark_ready`/`mark_failed`, and the transition that
        makes a failed ingestion recoverable without re-uploading the file --
        the bytes are still in object storage, so only the parse needs
        repeating.

        Deliberately *not* guarded on the current status. Retrying a `failed`
        document is the obvious case, but re-ingesting a `ready` one is just as
        legitimate (a chunk-size change, a new embedding model), and a
        `processing` one is already where this would put it. There is no
        status from which "try again" is meaningless, so there is nothing to
        refuse. The failure reason is cleared because it describes the previous
        attempt, and leaving it would show a stale error beside a running job.
        """
        self.status = DocumentStatus.PROCESSING
        self.failure_reason = None

    def soft_delete(self, *, now: datetime) -> None:
        if self.deleted_at is not None:
            raise InvalidStateTransitionError("document already deleted")
        self.deleted_at = now


class DataSourceKind(StrEnum):
    """Where a knowledge base's content comes from.

    `UPLOAD` exists in the vocabulary because docs/16 defines it, but uploaded
    documents do not currently create a `data_sources` row -- they are their
    own record. Kept so the CHECK constraint and this enum agree, and so an
    upload source can be introduced later without a migration.
    """

    UPLOAD = "upload"
    URL_CRAWL = "url_crawl"
    INTEGRATION_SYNC = "integration_sync"
    #: Text pasted straight into the console. Still a *source*, not a shortcut
    #: past one: it produces a `documents` row and flows through the same
    #: parse-free chunk -> embed -> upsert path as everything else, so
    #: retrieval, citation, re-index and delete all work without a second
    #: implementation.
    DIRECT_TEXT = "direct_text"


class SyncStatus(StrEnum):
    IDLE = "idle"
    SYNCING = "syncing"
    READY = "ready"
    ERROR = "error"


class CrawlMode(StrEnum):
    """What a crawl source means by its list of URLs.

    Mirrors `application.ai_resources.ports.CrawlMode`. Duplicated rather than
    imported because `domain` has zero project-internal imports by contract
    (docs/20) -- the domain cannot depend on the application layer, and this
    concept genuinely belongs to both.
    """

    URL_LIST = "url_list"
    SITE = "site"


@dataclass(kw_only=True)
class DataSource(Entity):
    """A crawl feeding a knowledge base.

    The URLs live on the entity rather than in an opaque blob so the invariants
    below are enforceable at all: a `url_crawl` with no URLs is a row that can
    never do anything, and a `SITE` crawl of five different start URLs is five
    crawls wearing one row's status field.
    """

    tenant_id: UUID
    knowledge_base_id: UUID
    kind: DataSourceKind
    urls: list[str]
    mode: CrawlMode
    created_by_membership_id: UUID
    #: Display name, used by every kind. For DIRECT_TEXT it is what the
    #: console lists and what the resulting document is called.
    name: str | None = None
    #: DIRECT_TEXT only: the pasted content. Held on the entity rather than in
    #: `config` so the length invariant below is enforceable, and so the
    #: re-index path has somewhere to read the current text from without
    #: re-fetching anything.
    text: str | None = None
    sync_status: SyncStatus = SyncStatus.IDLE
    failure_reason: str | None = None
    pages_discovered: int = 0
    pages_indexed: int = 0
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.kind is DataSourceKind.DIRECT_TEXT:
            if not (self.text or "").strip():
                raise ValueError("direct text cannot be empty")
            if len(self.text or "") > MAX_DIRECT_TEXT_CHARS:
                raise ValueError(
                    f"direct text must be {MAX_DIRECT_TEXT_CHARS} characters or fewer"
                )
            # A direct-text source crawls nothing, so the URL invariants below
            # do not apply and the site-crawl check would reject it outright.
            return
        if self.kind is DataSourceKind.URL_CRAWL and not self.urls:
            raise ValueError("a url_crawl data source needs at least one URL")
        if self.mode is CrawlMode.SITE and len(self.urls) != 1:
            # A site crawl follows links from *a* starting point. Several start
            # URLs would be several crawls sharing one status and one page
            # budget, and neither could be reported honestly.
            raise ValueError("a site crawl takes exactly one starting URL")

    def mark_syncing(self) -> None:
        self.sync_status = SyncStatus.SYNCING
        self.failure_reason = None
        self.pages_discovered = 0
        self.pages_indexed = 0

    def mark_ready(self, *, discovered: int, indexed: int, at: datetime) -> None:
        self.sync_status = SyncStatus.READY
        self.failure_reason = None
        self.pages_discovered = discovered
        self.pages_indexed = indexed
        self.last_synced_at = at

    def mark_failed(self, *, reason: str) -> None:
        self.sync_status = SyncStatus.ERROR
        self.failure_reason = reason


class WidgetStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


def normalise_origin(value: str | None) -> str:
    """Reduces a URL to the bare origin a browser would actually send.

    `https://Site.Example/a/page.html?q=1` -> `https://site.example`.

    Lives here, beside the only rule that consumes it, so the value stored by
    the write path and the value compared by `permits_origin` cannot drift --
    the same reason the vector-namespace parser sits beside its builder.

    Returns `""` for anything without both a scheme and a host, which
    `permits_origin` treats as "matches nothing". Refusing to guess is the
    point: a bare `site.example` could be a host or a path, and inventing a
    scheme for it would mean an `http://` entry silently permitting `https://`
    or the reverse.

    The port is deliberately **kept**. `https://site.example:8443` and
    `https://site.example` are different origins to a browser, and collapsing
    them would permit a site the tenant never listed.
    """
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


@dataclass(kw_only=True)
class ChatWidget(Entity):
    """A public question-answering surface for one knowledge base.

    The origin check lives here rather than in the route because it is a
    *policy* decision about what this widget permits, and putting it in the
    domain means every caller gets it -- including ones added later.
    """

    tenant_id: UUID
    knowledge_base_id: UUID
    name: str
    #: Public by construction: it sits in a script tag on a public page. Not a
    #: credential, and nothing here treats it as one.
    public_key: str
    allowed_origins: list[str]
    status: WidgetStatus = WidgetStatus.ACTIVE
    daily_question_limit: int = 500
    created_by_membership_id: UUID

    #: Which assistant answers here. `None` keeps the pre-existing behaviour
    #: exactly: platform default model, no persona.
    assistant_id: UUID | None = None
    #: How this embed introduces itself. Per-widget, because a tenant may run
    #: one on a parent portal and another on a public site.
    chatbot_name: str | None = None
    chatbot_title: str | None = None
    #: An asset *key* from a fixed allowlist, never a URL -- see
    #: `domain.ai_resources.chatbot.DEFAULT_AVATAR_KEY`.
    avatar_key: str | None = None
    greeting: str | None = None
    show_quick_reply_suggestions: bool = True
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status is WidgetStatus.ACTIVE

    def permits_origin(self, origin: str | None) -> bool:
        """Exact, case-insensitive match against the allowlist, origin to origin.

        **No wildcards and no suffix matching**, deliberately. `*.example.com`
        looks convenient and is how origin checks get broken: a naive
        `endswith(".example.com")` also accepts `evil-example.com`, and getting
        wildcard matching right is a parsing problem nobody needs to have. A
        tenant with three subdomains lists three origins.

        An empty allowlist permits nothing. A widget that has not been told
        where it may run is unusable rather than usable everywhere -- the
        opposite default would make a half-configured widget a public endpoint
        for anyone who read its key.

        **Both sides are reduced to a bare origin before comparing**, and that
        is a correctness fix rather than a relaxation. A browser's `Origin`
        header is `scheme://host[:port]` and never carries a path (RFC 6454),
        so an allowlist entry stored as a full page URL --
        `https://site.example/a/page.html`, which is exactly what someone
        pastes when asked where their widget lives -- could never match
        anything a browser would send. The widget simply never worked. It is
        not possible to permit one *page* and refuse another on the same site,
        because the browser does not tell us which page is asking; treating the
        entry as the origin it belongs to is the only coherent reading of it.

        **What this is worth:** browsers set `Origin` and page JavaScript
        cannot forge it, so this genuinely stops another *website* embedding
        the widget. It does not stop a non-browser client sending any origin it
        likes. Against that, the real defences are the rate limit and the daily
        cap, not this check -- see docs/03-threat-model.md.
        """
        if origin is None or not self.allowed_origins:
            return False
        candidate = normalise_origin(origin)
        if not candidate:
            return False
        return any(normalise_origin(allowed) == candidate for allowed in self.allowed_origins)

    def disable(self) -> None:
        self.status = WidgetStatus.DISABLED

    def enable(self) -> None:
        self.status = WidgetStatus.ACTIVE


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ConversationState(StrEnum):
    """Who is answering right now.

    Deliberately **one column, not a spread of booleans.** `is_handed_off`,
    `is_claimed` and `is_resolved` as separate flags admit combinations that
    mean nothing (handed off but not claimed and also resolved), and every
    reader then has to invent its own precedence rule. A single state makes the
    illegal combinations unrepresentable and the transitions reviewable.

    `status` (active/archived) is kept and is orthogonal: it answers "is this
    thread in the list?", this answers "who replies next?".
    """

    #: The AI answers. The state every new conversation starts in when the
    #: tenant has the chatbot enabled.
    AI_ACTIVE = "ai_active"
    #: A handoff has been asked for but no team is chosen yet -- the visitor
    #: has been offered the team buttons and has not pressed one.
    HANDOFF_REQUESTED = "handoff_requested"
    #: Waiting in a team's queue. This is the state the Unassigned inbox lists
    #: and the one that raises a notification.
    UNASSIGNED = "unassigned"
    #: An agent has claimed it but has not spoken yet.
    ASSIGNED = "assigned"
    #: An agent is answering.
    HUMAN_ACTIVE = "human_active"
    RESOLVED = "resolved"


#: Once a human owns the conversation the AI must not speak again on its own.
#: Held as a frozenset rather than checked inline because three separate call
#: sites depend on it and a fourth will be added by whoever adds the next
#: entry point -- a rule spread across call sites is a rule that drifts.
HUMAN_OWNED_STATES = frozenset(
    {
        ConversationState.HANDOFF_REQUESTED,
        ConversationState.UNASSIGNED,
        ConversationState.ASSIGNED,
        ConversationState.HUMAN_ACTIVE,
    }
)


class HandoffInitiator(StrEnum):
    VISITOR = "visitor"
    AI = "ai"
    AGENT = "agent"


class MessageRole(StrEnum):
    """What kind of turn this is, and therefore who may read it.

    `USER`/`ASSISTANT` are the platform's existing vocabulary and are kept as
    the equivalents of "visitor message" and "AI message" -- renaming them
    would rewrite every stored row to say the same thing in different words.
    The three new members are genuinely new kinds of turn that had nowhere to
    live before.

    **`INTERNAL_COMMENT` is the privacy-critical one.** It is staff-only by
    construction: `visible_to_visitor` is the single predicate every visitor
    -facing read path filters on, so a new surface cannot forget the rule by
    forgetting to add a condition.
    """

    USER = "user"
    ASSISTANT = "assistant"
    AGENT = "agent_message"
    INTERNAL_COMMENT = "internal_comment"
    SYSTEM_EVENT = "system_event"

    @property
    def visible_to_visitor(self) -> bool:
        return self not in (MessageRole.INTERNAL_COMMENT,)


@dataclass(kw_only=True)
class ConversationMessage(Entity):
    """One turn. Append-only: a thing that was said cannot be un-said, only the
    whole conversation deleted (the table revokes UPDATE from the app role)."""

    tenant_id: UUID
    conversation_id: UUID
    #: 1-based position, so "everything after the summary" is an index range.
    seq: int
    role: MessageRole
    content: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    token_count: int = 0
    created_at: datetime


@dataclass(kw_only=True)
class Conversation(Entity):
    """One thread, whoever is on the other end of it.

    **A conversation has exactly one owner, and it is either a member or a
    visitor.** `membership_id` was NOT NULL until widget conversations needed
    persisting, which would have meant either a second chat system (rejected:
    the handoff inbox, the message types and the audit trail would all have had
    to be built twice) or a nullable owner. The constraint is not weakened but
    *moved*: a database CHECK enforces that exactly one of `membership_id` and
    `visitor_session_id` is set, which is strictly more precise than "not
    null" -- it also rules out a row claiming to be both.
    """

    tenant_id: UUID
    #: Nullable because a widget conversation has no assistant when the widget
    #: predates assistant binding. Answering then uses the platform default,
    #: exactly as it did before.
    assistant_id: UUID | None = None
    #: The member who owns this thread. `None` for a visitor conversation.
    membership_id: UUID | None = None
    #: The widget session that owns this thread. `None` for a member's.
    #: Not a user id and not stable across sessions -- a visitor has no
    #: identity, by construction (see `WidgetSessionClaims`).
    visitor_session_id: UUID | None = None
    widget_id: UUID | None = None
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    state: ConversationState = ConversationState.AI_ACTIVE
    assigned_team_id: UUID | None = None
    assigned_membership_id: UUID | None = None
    handoff_reason: str | None = None
    handoff_at: datetime | None = None
    handoff_initiated_by: HandoffInitiator | None = None
    claimed_at: datetime | None = None
    #: Set by an agent who wants to keep this thread, overriding the automatic
    #: return-to-AI. It is a *decision*, not a timer state, which is why it is
    #: stored on the row: a refresh, a second tab, or a different agent picking
    #: the conversation up must all see the same answer.
    ai_fallback_disabled: bool = False
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    #: A rolling precis of turns already compacted, and how far it reaches.
    #: Together they bound the prompt: context is the summary plus the
    #: messages after `summary_through_seq`, never the whole thread.
    summary: str | None = None
    summary_through_seq: int = 0

    def archive(self, *, now: datetime) -> None:
        if self.status == ConversationStatus.ARCHIVED:
            raise InvalidStateTransitionError("conversation already archived")
        self.status = ConversationStatus.ARCHIVED
        self.updated_at = now

    def rename(self, title: str, *, now: datetime) -> None:
        cleaned = title.strip()
        if not cleaned:
            raise InvalidStateTransitionError("a conversation title cannot be empty")
        self.title = cleaned[:200]
        self.updated_at = now

    def record_turn(self, *, now: datetime) -> None:
        self.last_message_at = now
        self.updated_at = now

    def compact(self, *, summary: str, through_seq: int, now: datetime) -> None:
        """Replaces the compacted prefix with a precis.

        Refuses to move backwards: a stale job finishing after a newer one
        would otherwise re-expose turns the newer summary already covered.
        """
        if through_seq <= self.summary_through_seq:
            return
        self.summary = summary
        self.summary_through_seq = through_seq
        self.updated_at = now

    # -- handoff -------------------------------------------------------------

    @property
    def ai_may_reply(self) -> bool:
        """Whether the AI is still the one answering.

        **This is the guard behind requirement "AI must not automatically
        resume".** Once a human owns the thread, only an explicit
        `return_to_ai()` puts the AI back -- nothing about the visitor sending
        another message does, which is precisely the case that would otherwise
        let the AI talk over an agent mid-conversation.
        """
        return self.state is ConversationState.AI_ACTIVE

    def request_handoff(
        self, *, reason: str | None, initiated_by: HandoffInitiator, now: datetime
    ) -> None:
        """Asks for a human, without yet choosing a team.

        Idempotent for a thread already awaiting or receiving human attention:
        a visitor pressing "talk to a person" twice must not reset an
        already-claimed conversation back into the queue and strand the agent
        who claimed it.
        """
        if self.state in HUMAN_OWNED_STATES:
            return
        self.state = ConversationState.HANDOFF_REQUESTED
        self.handoff_reason = (reason or "").strip()[:500] or None
        self.handoff_initiated_by = initiated_by
        self.handoff_at = now
        self.updated_at = now

    def route_to_team(
        self,
        *,
        team_id: UUID,
        reason: str | None = None,
        initiated_by: HandoffInitiator | None = None,
        now: datetime,
    ) -> None:
        """Places the thread in a team's queue. The AI stops here."""
        if self.state in (ConversationState.ASSIGNED, ConversationState.HUMAN_ACTIVE):
            raise InvalidStateTransitionError(
                "this conversation is already with an agent"
            )
        self.state = ConversationState.UNASSIGNED
        self.assigned_team_id = team_id
        self.assigned_membership_id = None
        if reason is not None:
            self.handoff_reason = reason.strip()[:500] or None
        if initiated_by is not None:
            self.handoff_initiated_by = initiated_by
        if self.handoff_at is None:
            self.handoff_at = now
        self.updated_at = now

    def claim(self, *, membership_id: UUID, now: datetime) -> None:
        """One agent takes ownership.

        **This entity method is not what makes claiming safe.** Two agents can
        both load an UNASSIGNED conversation and both call this. The race is
        settled in the repository by a conditional `UPDATE ... WHERE state =
        'unassigned'`, which Postgres serialises; the loser sees zero rows
        affected and is told someone else got there first. This method exists
        so the in-memory object matches what was written -- it is the
        bookkeeping, not the lock.
        """
        if self.state not in (
            ConversationState.UNASSIGNED,
            ConversationState.HANDOFF_REQUESTED,
        ):
            raise InvalidStateTransitionError(
                "only an unassigned conversation can be claimed"
            )
        self.state = ConversationState.ASSIGNED
        self.assigned_membership_id = membership_id
        self.claimed_at = now
        self.updated_at = now

    def record_agent_reply(self, *, now: datetime) -> None:
        self.state = ConversationState.HUMAN_ACTIVE
        self.last_message_at = now
        self.updated_at = now

    def resolve(self, *, now: datetime) -> None:
        self.state = ConversationState.RESOLVED
        self.updated_at = now

    def return_to_ai(self, *, now: datetime) -> None:
        """The explicit, supported way back to the AI.

        Required to exist as its own transition rather than falling out of some
        other action: "the AI resumed because the agent went quiet" is exactly
        the accident requirement 10 forbids, and only a deliberate call can be
        audited as a deliberate decision.
        """
        self.state = ConversationState.AI_ACTIVE
        self.assigned_membership_id = None
        self.assigned_team_id = None
        self.updated_at = now


@dataclass(kw_only=True)
class ModelConfiguration(Entity):
    #: ``None`` => platform-owned. Retained rather than removed: it is the
    #: only ownership marker on the row, and tenant-owned configurations
    #: predate the entitlement model and are still in use. *Availability* is
    #: no longer derived from it -- that is `tenant_model_configurations`.
    tenant_id: UUID | None
    provider_credential_id: UUID | None = None
    model_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    token_budget_per_month: int | None = None
    created_at: datetime
    updated_at: datetime
    #: Set when the platform withdraws a configuration from further use.
    #: Deliberately not a delete: assistants already pointing at it keep
    #: working, and the audit trail keeps its referent.
    archived_at: datetime | None = None

    # -- provider configuration ---------------------------------------------
    #
    # These replace the single set of `OPENAI__*` environment variables that
    # every tenant on the deployment previously shared. `.env` remains the
    # fallback for a deployment that has configured nothing, so upgrading does
    # not require an operator to fill this in before the platform answers a
    # question -- but a configured row wins, and it is per-configuration.

    provider: str = "openai"
    #: **Ciphertext only.** The plaintext key never occupies a field on this
    #: entity, so no response DTO built from it can leak one by accident --
    #: the same "a DTO with no field capable of carrying the secret" argument
    #: that `ProviderCredentialSummary` rests on. Decryption happens in the
    #: chat/embedding adapter at call time and nowhere else.
    api_key_ciphertext: bytes | None = None
    #: Last four characters, for the console to show which key is in place.
    api_key_hint: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    #: The model actually called. `model_name` stays the human-facing label,
    #: which is what the tenant's assistant picker shows; a platform operator
    #: may well name a configuration "Fast (nursery)" while pointing it at
    #: `gpt-5.4-mini`.
    chat_model: str | None = None
    request_timeout_seconds: int | None = None
    chat_reasoning_effort: str | None = None
    #: A disabled configuration answers nothing. Distinct from archived:
    #: archiving withdraws it from *new* assignments and leaves working
    #: assistants alone, disabling stops it being used at all -- the switch an
    #: operator reaches for when a key is leaking.
    enabled: bool = True

    @property
    def is_platform_owned(self) -> bool:
        return self.tenant_id is None

    @property
    def has_own_credential(self) -> bool:
        return self.api_key_ciphertext is not None

    def set_api_key(self, *, ciphertext: bytes, hint: str, now: datetime) -> None:
        """Replaces the stored key. Rotation is this, not a separate path.

        Takes ciphertext rather than plaintext deliberately: the entity is the
        wrong place to hold an encryptor, and a method taking a plaintext key
        is a method whose arguments show up in a traceback.
        """
        self.api_key_ciphertext = ciphertext
        self.api_key_hint = hint
        self.updated_at = now

    def clear_api_key(self, *, now: datetime) -> None:
        """Falls back to the deployment's own key for this configuration."""
        self.api_key_ciphertext = None
        self.api_key_hint = None
        self.updated_at = now

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self, *, now: datetime) -> None:
        """Stops this configuration being offered for new assignments.

        Existing assistants are untouched by design -- archiving is how a
        platform operator retires a model without breaking every tenant that
        already chose it. Removing it from a tenant entirely is a separate,
        deliberately harder action (revoking the entitlement), which the
        database refuses while an assistant still depends on it.
        """
        if self.archived_at is None:
            self.archived_at = now
            self.updated_at = now

    def restore(self, *, now: datetime) -> None:
        if self.archived_at is not None:
            self.archived_at = None
            self.updated_at = now

    def update_details(
        self,
        *,
        model_name: str,
        parameters: dict[str, Any],
        token_budget_per_month: int | None,
        provider_credential_id: UUID | None,
        now: datetime,
        provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
        chat_model: str | None = None,
        request_timeout_seconds: int | None = None,
        chat_reasoning_effort: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.model_name = model_name
        self.parameters = parameters
        self.token_budget_per_month = token_budget_per_month
        self.provider_credential_id = provider_credential_id
        # The provider block is keyword-optional so the pre-existing callers
        # (and their tests) keep working unchanged; passing nothing leaves the
        # stored provider configuration exactly as it was.
        if provider is not None:
            self.provider = provider
        if enabled is not None:
            self.enabled = enabled
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self.chat_model = chat_model
        self.request_timeout_seconds = request_timeout_seconds
        self.chat_reasoning_effort = chat_reasoning_effort
        self.updated_at = now


class CredentialOwnerType(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


@dataclass(kw_only=True)
class ProviderCredential(Entity):
    """The ciphertext lives on this entity but is never returned by any read
    API -- only ``key_hint`` is ever surfaced (docs/16-schema-ai-resources.md).
    Enforcing that is the API layer's job; what the domain guarantees is that
    a revoked credential can never be handed to a model call.
    """

    owner_type: CredentialOwnerType
    tenant_id: UUID | None  # required iff owner_type == TENANT
    provider: str
    credential_ciphertext: bytes
    key_hint: str
    created_by_user_id: UUID
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def rotate(self, *, ciphertext: bytes, key_hint: str, now: datetime) -> None:
        if self.revoked_at is not None:
            raise InvalidStateTransitionError("cannot rotate a revoked credential")
        self.credential_ciphertext = ciphertext
        self.key_hint = key_hint
        self.rotated_at = now

    def revoke(self, *, now: datetime) -> None:
        if self.revoked_at is not None:
            raise InvalidStateTransitionError("credential already revoked")
        self.revoked_at = now
