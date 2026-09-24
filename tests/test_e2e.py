"""Live end-to-end tests. Skipped unless a server is configured.

    GOODMEM_BASE_URL=… GOODMEM_API_KEY=… GOODMEM_EMBEDDER_ID=… \
    GOODMEM_VERIFY_SSL=0 pytest -m integration

There is no default credential. Everything created here is deleted in the
fixture teardown, and the teardown asserts it is gone.
"""

from __future__ import annotations

import os
from typing import Any
import uuid
import warnings

from goodmem import AsyncGoodmem
from nlweb_core.retrieved_item import RetrievedItem
import pytest

from nlweb_goodmem import (
    GoodMemObjectLookupProvider,
    GoodMemRetrievalProvider,
    GoodMemSpaceError,
    upload_documents,
)
from nlweb_goodmem._spaces import find_by_name

pytestmark = pytest.mark.integration

BASE_URL = os.getenv("GOODMEM_BASE_URL")
API_KEY = os.getenv("GOODMEM_API_KEY")
EMBEDDER_ID = os.getenv("GOODMEM_EMBEDDER_ID")
RERANKER_ID = os.getenv("GOODMEM_RERANKER_ID")
VERIFY_SSL = os.getenv("GOODMEM_VERIFY_SSL", "1") not in ("0", "false", "False")
BOGUS = "00000000-0000-0000-0000-000000000000"

if not (BASE_URL and API_KEY and EMBEDDER_ID):
    pytest.skip(
        "set GOODMEM_BASE_URL, GOODMEM_API_KEY and GOODMEM_EMBEDDER_ID",
        allow_module_level=True,
    )

RECIPES = "recipes.example.com"
FILMS = "films.org"
DOCS = [
    ({"@type": "Recipe", "url": "https://ex.com/r/laksa", "name": "Singapore Laksa",
      "description": "A coconut curry noodle soup with prawns and tofu puffs."}, RECIPES),
    ({"@type": "Recipe", "url": "https://ex.com/r/pho", "name": "Beef Pho",
      "description": "Vietnamese rice noodle soup simmered with charred ginger."}, RECIPES),
    ({"@type": "Movie", "url": "https://films.org/m/dune", "name": "Dune",
      "description": "A noble family becomes embroiled in a war over Arrakis."}, FILMS),
]


@pytest.fixture(scope="module")
async def live() -> Any:
    client = AsyncGoodmem(base_url=BASE_URL, api_key=API_KEY, verify=VERIFY_SSL)
    name = f"nlweb-goodmem-e2e-{uuid.uuid4().hex[:8]}"
    space = await client.spaces.create(
        name=name,
        space_embedders=[{"embedderId": EMBEDDER_ID, "defaultRetrievalWeight": 1.0}],
        default_chunking_config={"recursive": {
            "chunkSize": 256, "chunkOverlap": 25,
            "separators": ["\n\n", "\n", ". ", " ", ""], "keepStrategy": "KEEP_END",
            "separatorIsRegex": False, "lengthMeasurement": "CHARACTER_COUNT"}},
    )
    provider = GoodMemRetrievalProvider(space_id=space.space_id, client=client)
    for document, site in DOCS:
        await upload_documents(provider, [document], site=site)

    yield {"client": client, "space_id": space.space_id, "space_name": name}

    await client.spaces.delete(id=space.space_id)
    assert await find_by_name(client, name) == [], "teardown left the space behind"
    await client.close()


@pytest.fixture
async def provider(live: Any) -> GoodMemRetrievalProvider:
    return GoodMemRetrievalProvider(space_id=live["space_id"], client=live["client"])


async def test_schema_org_documents_round_trip(provider) -> None:
    items = await provider.search("spicy coconut noodle soup", RECIPES, num_results=5)
    assert items and isinstance(items[0], RetrievedItem)
    assert items[0].schema_object[0]["@type"] == "Recipe"
    assert items[0].site == RECIPES
    assert items[0].url.startswith("https://")


async def test_site_filtering_scopes_results(provider) -> None:
    recipes = await provider.search("noodle soup", RECIPES, num_results=10)
    films = await provider.search("noodle soup", FILMS, num_results=10)
    assert {i.site for i in recipes} == {RECIPES}
    assert {i.site for i in films} == {FILMS}


async def test_several_sites_are_searched_together(provider) -> None:
    items = await provider.search("noodle soup", [RECIPES, FILMS], num_results=10)
    assert {i.site for i in items} == {RECIPES, FILMS}


async def test_search_all_sites(provider) -> None:
    items = await provider.search_all_sites("desert planet Arrakis", num_results=10)
    assert any(i.site == FILMS for i in items)


async def test_an_unknown_site_is_empty_not_an_error(provider) -> None:
    assert await provider.search("anything", "nope.example.com", num_results=5) == []


async def test_a_quote_in_a_site_is_escaped(provider) -> None:
    assert await provider.search("noodle", "x' OR '1'='1", num_results=10) == []


async def test_get_sites_lists_what_is_there(provider) -> None:
    assert set(await provider.get_sites()) == {RECIPES, FILMS}


async def test_object_lookup_by_url(live) -> None:
    lookup = GoodMemObjectLookupProvider(space_id=live["space_id"], client=live["client"])
    obj = await lookup.get_by_id("https://films.org/m/dune")
    assert obj and obj["name"] == "Dune" and obj["@type"] == "Movie"
    assert await lookup.get_by_id("https://ex.com/does-not-exist") is None


async def test_a_broken_reranker_keeps_its_results(live) -> None:
    bad = GoodMemRetrievalProvider(
        space_id=live["space_id"], client=live["client"], reranker_id=BOGUS
    )
    assert await bad.search("noodle soup", RECIPES, num_results=5)


async def test_no_results_plus_a_problem_warns_and_does_not_raise(live) -> None:
    bad = GoodMemRetrievalProvider(
        space_id=live["space_id"], client=live["client"], reranker_id=BOGUS
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert await bad.search("noodle", "nope.example.com", num_results=5) == []
    assert caught, "a failed retrieval must be visible somewhere"


async def test_a_working_reranker_is_not_degraded(live) -> None:
    if not RERANKER_ID:
        pytest.skip("set GOODMEM_RERANKER_ID")
    good = GoodMemRetrievalProvider(
        space_id=live["space_id"], client=live["client"], reranker_id=RERANKER_ID
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert await good.search("noodle soup", RECIPES, num_results=5)


async def test_attach_by_name_refuses_a_different_embedder(live) -> None:
    p = GoodMemRetrievalProvider(
        space_name=live["space_name"], embedder_id=BOGUS, client=live["client"]
    )
    with pytest.raises(GoodMemSpaceError, match="Retrieval across mismatched"):
        await p.search("noodle", "all")


async def test_close_does_not_close_an_injected_client(live) -> None:
    p = GoodMemRetrievalProvider(space_id=live["space_id"], client=live["client"])
    await p.close()
    assert await live["client"].spaces.get(id=live["space_id"])
