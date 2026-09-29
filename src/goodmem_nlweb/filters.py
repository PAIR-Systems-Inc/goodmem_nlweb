"""Safe construction of GoodMem metadata filter expressions.

GoodMem filters are expression strings evaluated server-side. Building one by
interpolating caller data straight into the string is an injection risk, so
these helpers quote values and refuse field names that cannot be expressed
safely.

``val()`` returns JSON, so a comparison needs an explicit cast, and the cast
has to match the stored type: ``TEXT`` for strings, ``NUMERIC`` for numbers,
``BOOLEAN`` for booleans. A mismatched cast is not an error -- the server
answers HTTP 200 and matches nothing, which looks exactly like "nothing
stored". So :func:`from_mapping` casts each value by its Python type:

========================  ==========================================
Python value              expression
========================  ==========================================
``"feat"`` (``str``)      ``CAST(val('$.f') AS TEXT) = 'feat'``
``True`` (``bool``)       ``CAST(val('$.f') AS BOOLEAN) = true``
``5`` / ``2.5``           ``CAST(val('$.f') AS NUMERIC) = 5`` / ``2.5``
``None`` and other types  ``ValueError``
========================  ==========================================

Escaping and casting were established against a live server (v1.0.320), not
assumed. The grammar escapes with a backslash: SQL-style ``''`` doubling and
double-quoted strings are both rejected with HTTP 400, and a raw newline
inside a literal is rejected outright. A stored ``{"flag": true, "n": 5}``
is matched by ``CAST(val('$.flag') AS BOOLEAN) = true`` and
``CAST(val('$.n') AS NUMERIC) = 5``. Compared as ``TEXT``, the boolean is
the server's own spelling ``'t'``, so ``'True'`` (``str(True)``) and
``'true'`` match nothing; ``'5.0'`` does not match the ``'5'`` of a stored 5;
and ``AS NUMBER`` is an HTTP 500.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import math
import re

# A JSONPath member we are willing to build without escaping games.
_SAFE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# The filter grammar has no encoding for these inside a literal.
_FORBIDDEN_IN_LITERAL = re.compile(r"[\x00-\x1f\x7f]")


def _quote(value: str) -> str:
    """Single-quote a literal, backslash-escaping backslashes and quotes.

    Order matters: backslashes are escaped first so the backslash introduced
    for a quote is not escaped a second time.
    """
    if _FORBIDDEN_IN_LITERAL.search(value):
        raise ValueError(
            "Metadata filter values cannot contain control characters; the "
            "GoodMem filter grammar rejects them."
        )
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _check_field(field: str) -> str:
    if not isinstance(field, str) or not _SAFE_FIELD.match(field):
        raise ValueError(
            f"Unsupported metadata field name {field!r}. Use letters, digits "
            "and underscores, or pass a filter expression directly."
        )
    return field


def _number(value: float) -> str:
    """Render a number as a plain decimal literal.

    ``repr`` would give ``nan``, ``inf`` or exponent notation (``1e+20``);
    the grammar has no literal for the first two, so they are refused.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Metadata filter numbers must be finite.")
        return format(Decimal(repr(float(value))), "f")
    return str(int(value))


def _typed(value: object) -> tuple[str, str]:
    """The cast and the literal for ``value``, chosen by its Python type."""
    # bool before int: bool is a subclass of int, and a boolean compared as
    # anything but BOOLEAN matches nothing.
    if isinstance(value, bool):
        return "BOOLEAN", "true" if value else "false"
    if isinstance(value, (int, float)):
        return "NUMERIC", _number(value)
    if isinstance(value, str):
        return "TEXT", _quote(value)
    raise ValueError(
        f"Unsupported metadata filter value {value!r} "
        f"({type(value).__name__}); use str, int, float or bool."
    )


def text_equals(field: str, value: str) -> str:
    """Build an equality comparison against a text metadata field."""
    return f"CAST(val('$.{_check_field(field)}') AS TEXT) = {_quote(value)}"


def from_mapping(
    metadata_filter: Mapping[str, str | int | float | bool],
) -> str | None:
    """AND-join a mapping of field/value pairs into one filter expression.

    Each value is compared under the cast its Python type calls for: ``str``
    as ``TEXT``, ``bool`` as ``BOOLEAN`` and ``int``/``float`` as
    ``NUMERIC``. So ``{"flag": True}`` matches a stored JSON ``true`` and
    ``{"flag": "true"}`` matches the stored string ``"true"``. ``None`` and
    any other type raise ``ValueError`` rather than being stringified into a
    filter that matches nothing.

    Returns ``None`` for an empty mapping so callers can skip the filter.
    """
    clauses = []
    for field, value in metadata_filter.items():
        cast, literal = _typed(value)
        clauses.append(f"CAST(val('$.{_check_field(field)}') AS {cast}) = {literal}")
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return " AND ".join(f"({clause})" for clause in clauses)


def combine(*expressions: str | None) -> str | None:
    """AND-join already-built expressions, ignoring ``None``."""
    present = [e for e in expressions if e]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return " AND ".join(f"({e})" for e in present)
