"""Request-id and content-hash helpers for v0.5.4 multi-agent safety.

- ``new_request_id()`` returns a UUID7 (time-ordered, unique per call) so
  audit events can be correlated across processes and clients can do
  idempotent retries.
- ``content_sha()`` returns a deterministic SHA-256 hex digest of file
  bytes; used by the optional ``if_match`` optimistic-locking parameter
  on ``memory_write``.
"""

from __future__ import annotations

import hashlib
import uuid


def new_request_id() -> str:
    """Return a fresh UUID7 string.

    UUID7 (RFC 9562) is a time-ordered UUID: the leading bits are a
    millisecond Unix timestamp, the trailing bits are random, so values
    sort lexicographically by creation time without colliding under
    high concurrency. Available in Python 3.14+.
    """
    return str(uuid.uuid7())


def content_sha(content: str) -> str:
    """Return the SHA-256 hex digest of ``content`` (UTF-8 encoded).

    Used as the lightweight ``ETag`` for optimistic-locking writes:
    callers read the file, compute its sha, then pass it back as
    ``if_match`` on the next write. The server compares against the
    current on-disk sha and rejects mismatches.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__ = ["new_request_id", "content_sha"]
