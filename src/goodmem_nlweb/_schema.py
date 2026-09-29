"""Mapping between Schema.org objects and GoodMem memories.

NLWeb speaks Schema.org: every retrieved item is ``(url, raw_schema_object,
site)``. GoodMem stores text and metadata. The mapping is:

===================  ==========================================
NLWeb                GoodMem
===================  ==========================================
``url``              ``metadata["url"]`` -- also the identity
``site``             ``metadata["site"]`` -- what NLWeb filters on
``raw_schema_object````metadata["schema_json"]`` (a JSON string)
(text to embed)      the memory's ``original_content``
===================  ==========================================

The embedded text is *not* the raw JSON. Embedding a JSON blob buries the
words a query would match under punctuation and key names, so the text is
extracted from the fields a reader would actually read. The untouched object
is kept alongside, because NLWeb returns it verbatim to the caller.

This mirrors how NLWeb's own Qdrant provider stores things: a payload with
``url``, ``schema_json``, ``name`` and ``site``.
"""

from __future__ import annotations

import json
from typing import Any

#: Schema.org fields worth embedding, in the order a reader would meet them.
_TEXT_FIELDS = (
    "name",
    "headline",
    "alternateName",
    "description",
    "abstract",
    "text",
    "articleBody",
)

MAX_EMBED_CHARS = 8000


def schema_to_text(schema: Any) -> str:
    """Extract the human-readable text from a Schema.org object.

    Falls back to a compact JSON rendering only when no known text field is
    present, because retrieving nothing is worse than embedding a blob.
    """
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            return schema[:MAX_EMBED_CHARS]
    if isinstance(schema, list):
        return "\n\n".join(schema_to_text(item) for item in schema)[:MAX_EMBED_CHARS]
    if not isinstance(schema, dict):
        return str(schema)[:MAX_EMBED_CHARS]

    parts: list[str] = []
    for field in _TEXT_FIELDS:
        value = schema.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if not parts:
        return json.dumps(schema, ensure_ascii=False)[:MAX_EMBED_CHARS]
    return "\n\n".join(parts)[:MAX_EMBED_CHARS]


def schema_name(schema: Any) -> str:
    """The display name NLWeb shows for an item."""
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            return ""
    if isinstance(schema, list):
        return schema_name(schema[0]) if schema else ""
    if not isinstance(schema, dict):
        return ""
    for field in ("name", "headline", "alternateName"):
        value = schema.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def schema_url(schema: Any, fallback: str = "") -> str:
    """The URL NLWeb uses as an item's identity."""
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            return fallback
    if isinstance(schema, list):
        return schema_url(schema[0], fallback) if schema else fallback
    if not isinstance(schema, dict):
        return fallback
    for field in ("url", "@id", "identifier"):
        value = schema.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def to_memory_fields(
    document: Any, *, site: str = "", url: str = ""
) -> tuple[str, dict[str, Any]]:
    """Return ``(text_to_embed, metadata)`` for one Schema.org document.

    ``document`` may be the object itself, a JSON string, or an NLWeb-style
    wrapper carrying ``url`` / ``site`` / ``schema_json`` keys.
    """
    schema: Any = document
    if isinstance(document, dict) and (
        "schema_json" in document or "raw_schema_object" in document
    ):
        schema = document.get("schema_json") or document.get("raw_schema_object")
        site = document.get("site") or site
        url = document.get("url") or url

    resolved_url = url or schema_url(schema)
    if not resolved_url:
        raise ValueError(
            "Every NLWeb document needs a url: it is the item's identity and "
            "the key NLWeb deduplicates on. Pass url= or include one in the "
            "Schema.org object."
        )

    raw = schema if isinstance(schema, str) else json.dumps(schema, ensure_ascii=False)
    return schema_to_text(schema), {
        "url": resolved_url,
        "site": site,
        "name": schema_name(schema),
        "schema_json": raw,
    }
