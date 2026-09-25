"""The one check a GoodMem id passes before it can reach a URL path.

The goodmem SDK builds request paths by interpolating ids unencoded
(``f"/v1/spaces/{space_id}/memories"``, ``f"/v1/memories/{id}"``), and httpx
resolves dot segments before it sends. A space id of ``../spaces/<other>``
is therefore a request against a different space, and ``<id>#x`` or
``<id>?x=1`` cuts the path short -- measured, not assumed; see
``tests/test_ids.py``. Neither the client nor the server can be relied on to
stop that, so it is stopped here, before any request is made.

Every GoodMem id (space, memory, embedder, reranker, ...) is a UUID, so the
rule is simple: a canonical UUID is accepted and lowercased, and anything
else is refused.

Where it is called:

* configured ids are checked when the provider is built, so a bad option
  fails when NLWeb loads it rather than on the first request;
* an id that goes into a URL path is checked again immediately before the
  SDK call that uses it, whatever its source -- configuration, or the
  server's own answer.
"""

from __future__ import annotations

import re
from typing import Any
import uuid

# fullmatch, not ^...$: in a Python regex `$` also matches before a trailing
# newline, which would let "<uuid>\n" through.
_CANONICAL_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def require_uuid(value: Any, field: str) -> str:
    """Return ``value`` as a lowercase canonical UUID, or raise ``ValueError``.

    A ``uuid.UUID`` is accepted as-is. The looser spellings ``uuid.UUID()``
    parses -- braces, ``urn:uuid:``, no hyphens -- are refused: an id that is
    about to become a path segment has exactly one acceptable form.

    The refused value is not repeated in the message. A mistyped option can
    be a credential, and this message ends up in logs.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and _CANONICAL_UUID.fullmatch(value):
        return value.lower()
    raise ValueError(
        f"{field} must be a UUID (8-4-4-4-12 hexadecimal digits). GoodMem ids "
        "are UUIDs, and any other value is refused before a request is made "
        "because the SDK places ids in the URL path unencoded, where a value "
        "such as '../' would send the request to a different endpoint."
    )
