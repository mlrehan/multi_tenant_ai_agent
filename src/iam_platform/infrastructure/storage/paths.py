"""Server-side object-storage path generation -- docs/16-schema-ai-resources.md.

Trivially small on purpose. The value isn't the string formatting, it's that
this is the *only* way a ``storage_path`` comes into existence: the use case
takes a factory, not a path, so there is no signature anywhere that could
accept a client-supplied one.
"""

from __future__ import annotations

from uuid import UUID


class TenantScopedStoragePathFactory:
    def build(self, *, tenant_id: UUID, knowledge_base_id: UUID, document_id: UUID) -> str:
        return f"{tenant_id}/{knowledge_base_id}/{document_id}"
