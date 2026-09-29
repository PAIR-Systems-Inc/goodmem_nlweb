"""metadata_filter values are cast by their Python type (2026-09-28).

Before this fix every value was stringified and compared as TEXT, so
``metadata_filter={"flag": True}`` sent ``CAST(val('$.flag') AS TEXT) =
'True'``. Live (v1.0.320), with a memory whose metadata is ``{"flag": true,
"n": 5}``, that is accepted with HTTP 200 and matches nothing -- as does
``'true'``; the server's text for a boolean is ``'t'`` -- while
``CAST(val('$.flag') AS BOOLEAN) = true`` matches. A float fared no better:
``'5.0'`` does not equal the text of a stored 5. ``None`` became the string
``'None'``.

Everything here drives ``GoodMemRetrievalProvider.search`` through the real
SDK over a mock transport and asserts on the request that reaches the wire.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
import re
from typing import Any

import pytest
import yaml

from goodmem_nlweb import GoodMemRetrievalProvider, filters
from tests.conftest import Recorder, load_json, ndjson_events, ndjson_response

SPACE = load_json("space.json")["spaceId"]
RETRIEVE = "/v1/memories:retrieve"
README = Path(__file__).resolve().parent.parent / "README.md"


def provider(recorder: Recorder, client: Any) -> GoodMemRetrievalProvider:
    recorder.route(
        "POST", RETRIEVE, ndjson_response(ndjson_events("retrieve_all.ndjson"))
    )
    return GoodMemRetrievalProvider(space_id=SPACE, client=client)


async def sent_filter(
    recorder: Recorder, client: Any, metadata_filter: Any, site: str = "all"
) -> str | None:
    await provider(recorder, client).search(
        "q", site, num_results=5, metadata_filter=metadata_filter
    )
    assert len(recorder.requests) == 1
    return recorder.last_body["spaceKeys"][0].get("filter")


# ------------------------------------------------------ typed casts (defect)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "CAST(val('$.f') AS BOOLEAN) = true"),
        (False, "CAST(val('$.f') AS BOOLEAN) = false"),
        (5, "CAST(val('$.f') AS NUMERIC) = 5"),
        (0, "CAST(val('$.f') AS NUMERIC) = 0"),
        (-3, "CAST(val('$.f') AS NUMERIC) = -3"),
        (5.0, "CAST(val('$.f') AS NUMERIC) = 5.0"),
        (2.5, "CAST(val('$.f') AS NUMERIC) = 2.5"),
        (1e20, "CAST(val('$.f') AS NUMERIC) = 100000000000000000000"),
        (1.5e-7, "CAST(val('$.f') AS NUMERIC) = 0.00000015"),
    ],
)
async def test_each_value_is_cast_to_its_own_type(recorder, client, value, expected):
    assert await sent_filter(recorder, client, {"f": value}) == expected


async def test_bool_is_boolean_not_the_number_it_subclasses(recorder, client):
    """``isinstance(True, int)`` holds, so the bool check must come first."""
    expression = await sent_filter(recorder, client, {"flag": True})
    assert "BOOLEAN" in expression and "NUMERIC" not in expression
    assert "'True'" not in expression


async def test_several_fields_keep_their_order_and_their_own_casts(recorder, client):
    expression = await sent_filter(
        recorder, client, {"flag": True, "n": 5, "category": "x"}
    )
    assert expression == (
        "(CAST(val('$.flag') AS BOOLEAN) = true)"
        " AND (CAST(val('$.n') AS NUMERIC) = 5)"
        " AND (CAST(val('$.category') AS TEXT) = 'x')"
    )


async def test_a_typed_filter_is_and_ed_with_the_site(recorder, client):
    expression = await sent_filter(recorder, client, {"flag": True}, site="s.example")
    assert expression == (
        "(CAST(val('$.site') AS TEXT) = 's.example')"
        " AND (CAST(val('$.flag') AS BOOLEAN) = true)"
    )


# ------------------------------------------ refused, before any request
@pytest.mark.parametrize(
    "value",
    [None, ["a", "b"], {"a": 1}, b"bytes", Decimal("1.5"), object()],
    ids=["None", "list", "dict", "bytes", "Decimal", "object"],
)
async def test_values_without_a_typed_form_are_refused(recorder, client, value):
    """0.2.1 sent ``str(value)``: ``None`` became the text ``'None'``."""
    p = provider(recorder, client)
    with pytest.raises(ValueError, match="Unsupported metadata filter value"):
        await p.search("q", "all", metadata_filter={"flag": value})
    assert recorder.requests == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_numbers_are_refused(recorder, client, value):
    p = provider(recorder, client)
    with pytest.raises(ValueError, match="finite"):
        await p.search("q", "all", metadata_filter={"n": value})
    assert recorder.requests == []


def test_a_non_string_field_name_is_refused():
    """A YAML key such as ``1: true`` loads as an int."""
    with pytest.raises(ValueError, match="Unsupported metadata field name"):
        filters.from_mapping({1: True})  # type: ignore[dict-item]


# ------------------------------------------ controls: text is unchanged
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x", "CAST(val('$.f') AS TEXT) = 'x'"),
        ("it's", "CAST(val('$.f') AS TEXT) = 'it\\'s'"),
        ("a\\b", "CAST(val('$.f') AS TEXT) = 'a\\\\b'"),
        ("x' OR '1'='1", "CAST(val('$.f') AS TEXT) = 'x\\' OR \\'1\\'=\\'1'"),
        # A string is text even when it spells a boolean or a number.
        ("true", "CAST(val('$.f') AS TEXT) = 'true'"),
        ("5", "CAST(val('$.f') AS TEXT) = '5'"),
        ("", "CAST(val('$.f') AS TEXT) = ''"),
    ],
)
async def test_strings_are_still_escaped_text(recorder, client, value, expected):
    assert await sent_filter(recorder, client, {"f": value}) == expected
    assert filters.text_equals("f", value) == expected


def test_control_characters_in_text_are_still_refused():
    with pytest.raises(ValueError, match="control characters"):
        filters.from_mapping({"f": "a\nb"})


async def test_an_empty_mapping_sends_no_filter(recorder, client):
    assert filters.from_mapping({}) is None
    assert await sent_filter(recorder, client, {}) is None


# ------------------------------------------------------- the README says so
def _readme_section(title: str) -> str:
    text = README.read_text()
    start = text.index(f"## {title}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def test_the_readme_cast_table_is_what_from_mapping_builds():
    rows = re.findall(
        r"^\| `(\w+)`[^|]* \| \w+ \| `(CAST\(val\('\$\.(\w+)'\) AS \w+\) = [^`]+)` \|$",
        _readme_section("Metadata filters"),
        re.MULTILINE,
    )
    assert [r[0] for r in rows] == ["str", "bool", "int"]
    samples = {"str": "Malaysian", "bool": True, "int": 4}
    for kind, expression, field in rows:
        assert filters.from_mapping({field: samples[kind]}) == expression


async def test_the_readme_python_example_sends_typed_filters(recorder, client):
    section = _readme_section("Metadata filters")
    literal = re.search(r"metadata_filter=(\{[^}]*\})", section)
    assert literal
    assert await sent_filter(
        recorder, client, ast.literal_eval(literal.group(1)), site="recipes.example.com"
    ) == (
        "(CAST(val('$.site') AS TEXT) = 'recipes.example.com')"
        " AND ((CAST(val('$.vegetarian') AS BOOLEAN) = true)"
        " AND (CAST(val('$.servings') AS NUMERIC) = 4))"
    )


async def test_the_readme_yaml_filter_loads_typed(recorder, client):
    """YAML ``true`` is a bool and ``4`` an int; a quoted ``"true"`` stays a
    string. The documented block must come out BOOLEAN / NUMERIC / TEXT."""
    blocks = re.findall(
        r"```yaml\n(.*?)```", _readme_section("Metadata filters"), re.DOTALL
    )
    assert len(blocks) == 1
    loaded = yaml.safe_load(blocks[0])
    assert loaded == {"vegetarian": True, "servings": 4, "cuisine": "Malaysian"}
    assert await sent_filter(recorder, client, loaded) == (
        "(CAST(val('$.vegetarian') AS BOOLEAN) = true)"
        " AND (CAST(val('$.servings') AS NUMERIC) = 4)"
        " AND (CAST(val('$.cuisine') AS TEXT) = 'Malaysian')"
    )
    quoted = yaml.safe_load('vegetarian: "true"\n')
    assert filters.from_mapping(quoted) == "CAST(val('$.vegetarian') AS TEXT) = 'true'"
