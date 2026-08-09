"""UUIDv7 generation (RFC 9562) using only the standard library.

Time-ordered UUIDs keep B-tree index locality good at high insert volume,
per the decision recorded in docs/10-schema-conventions.md. Implemented
in-house (rather than pulling in a third-party uuidv7 package) since the
algorithm is small and stable, and PostgreSQL's own ``uuidv7()`` (18+) is
used for any ID generated inside the database (e.g. DEFAULT clauses); this
generator is for IDs the application must know before an INSERT executes.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7: 48-bit millisecond timestamp + 74 random bits."""
    unix_ms = time.time_ns() // 1_000_000
    timestamp_bytes = unix_ms.to_bytes(6, byteorder="big")

    rand = bytearray(os.urandom(10))
    # Set version (7) in the high nibble of byte 6, per RFC 9562 §5.7.
    rand[0] = (rand[0] & 0x0F) | 0x70
    # Set variant (10xxxxxx) in byte 8.
    rand[2] = (rand[2] & 0x3F) | 0x80

    raw = timestamp_bytes + bytes(rand)
    return uuid.UUID(bytes=raw)
