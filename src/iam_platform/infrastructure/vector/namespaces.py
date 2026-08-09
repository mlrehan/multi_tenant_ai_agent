"""Server-side vector-namespace generation -- docs/16-schema-ai-resources.md.

Same rationale as ``infrastructure.storage.paths``: the namespace is derived
from IDs the caller was already authorized for, never accepted as input, which
is what makes "vector queries always use server-generated tenant filters"
(Phase 1 §12) a structural property rather than a rule to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class TenantScopedVectorNamespaceFactory:
    def build(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> str:
        return f"{tenant_id}/{knowledge_base_id}"


@dataclass(frozen=True, slots=True)
class ParsedNamespace:
    tenant_id: UUID
    knowledge_base_id: UUID


def parse_namespace(namespace: str) -> ParsedNamespace:
    """Splits a namespace back into the two IDs that built it.

    Deliberately lives beside ``TenantScopedVectorNamespaceFactory.build``
    rather than in the Qdrant adapter: producer and parser of a wire format
    that must round-trip belong in one file, where a change to either is
    visibly a change to both. Putting the parsing in the vector-store client
    would let the format drift the moment someone edited ``build`` alone.

    The namespace is always server-generated (that is the whole point of the
    factory), so a malformed one is an internal bug, not hostile input --
    hence a loud ``ValueError`` rather than a lenient best-effort parse that
    would let a mis-derived namespace quietly address the wrong collection.
    """
    tenant_part, separator, knowledge_base_part = namespace.partition("/")
    if not separator:
        raise ValueError(
            f"malformed vector namespace {namespace!r}: expected '<tenant_id>/<knowledge_base_id>'"
        )
    try:
        return ParsedNamespace(
            tenant_id=UUID(tenant_part),
            knowledge_base_id=UUID(knowledge_base_part),
        )
    except ValueError as exc:
        raise ValueError(
            f"malformed vector namespace {namespace!r}: both segments must be UUIDs"
        ) from exc


def collection_name_for_tenant(tenant_id: UUID) -> str:
    """The Qdrant collection holding every vector for one tenant.

    **One collection per tenant, not per knowledge base** -- matching
    ``Architectural_Diagram.txt``'s ``tenant_university_a_xxxx`` sketch. Two
    reasons, one operational and one functional: Qdrant carries real
    per-collection overhead (segments, threads, file handles), so
    tenants x knowledge-bases collections would not survive the "thousands of
    tenants" this platform is sized for; and an assistant that draws on
    several knowledge bases can then be served by one filtered query instead
    of a fan-out-and-merge across collections.

    Isolation still lands on the collection boundary -- the security-critical
    one -- with ``knowledge_base_id`` as an in-collection payload filter. A
    cross-*tenant* read would require addressing a different collection
    entirely, which no code path can do: the collection name is derived here
    from a namespace that was itself server-derived from an already-authorized
    knowledge base.

    Hyphens are stripped because the name also appears in Qdrant REST paths;
    ``.hex`` keeps it unambiguous and URL-safe.
    """
    return f"tenant_{tenant_id.hex}"
