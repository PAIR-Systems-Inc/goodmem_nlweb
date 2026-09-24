"""Every test here fails against nlweb-goodmem 0.1.0.

Fixtures are bytes a live server (v1.0.320) actually sent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from nlweb_core.retrieved_item import RetrievedItem
from nlweb_core.retriever import ObjectLookupProvider, RetrievalProvider
import pytest

from nlweb_goodmem import (
    GoodMemObjectLookupProvider,
    GoodMemRetrievalProvider,
    GoodMemSpaceError,
    GoodMemUploadError,
    filters,
    schema_to_text,
    to_memory_fields,
    upload_documents,
)
from tests.conftest import Recorder, load_json, ndjson_events, ndjson_response

SPACE = load_json("space.json")["spaceId"]
RETRIEVE = "/v1/memories:retrieve"
SITE = "recipes.example.com"


def make(recorder: Recorder, client: Any, fixture: str, **kw: Any) -> GoodMemRetrievalProvider:
    recorder.route("POST", RETRIEVE, ndjson_response(ndjson_events(fixture)))
    return GoodMemRetrievalProvider(space_id=SPACE, client=client, **kw)


# ------------------------------------------------- the NLWeb contract
def test_it_is_a_real_nlweb_provider():
    """0.1.0 never imported nlweb at all."""
    assert issubclass(GoodMemRetrievalProvider, RetrievalProvider)
    assert not GoodMemRetrievalProvider.__abstractmethods__
    assert issubclass(GoodMemObjectLookupProvider, ObjectLookupProvider)
    assert not GoodMemObjectLookupProvider.__abstractmethods__


async def test_search_returns_retrieved_items(recorder, client):
    p = make(recorder, client, "retrieve_site.ndjson")
    items = await p.search("noodle soup", SITE, num_results=5)
    assert items and all(isinstance(i, RetrievedItem) for i in items)
    assert items[0].url.startswith("https://")
    assert items[0].site == SITE
    assert items[0].schema_object[0]["@type"] == "Recipe"


async def test_a_site_becomes_an_escaped_server_side_filter(recorder, client):
    p = make(recorder, client, "retrieve_site.ndjson")
    await p.search("noodle soup", SITE, num_results=5)
    assert recorder.last_body["spaceKeys"][0]["filter"] == (
        f"CAST(val('$.site') AS TEXT) = '{SITE}'"
    )


async def test_several_sites_are_or_ed(recorder, client):
    p = make(recorder, client, "retrieve_all.ndjson")
    await p.search("q", ["a.com", "b.com"], num_results=5)
    expression = recorder.last_body["spaceKeys"][0]["filter"]
    assert " OR " in expression and "a.com" in expression and "b.com" in expression


@pytest.mark.parametrize("site", ["all", "ALL", "*", "", None])
async def test_all_means_no_site_filter(recorder, client, site):
    p = make(recorder, client, "retrieve_all.ndjson")
    await p.search("q", site, num_results=5)
    assert "filter" not in recorder.last_body["spaceKeys"][0]


async def test_a_quote_in_a_site_is_escaped_not_injected(recorder, client):
    p = make(recorder, client, "retrieve_empty.ndjson")
    await p.search("q", "x' OR '1'='1", num_results=5)
    assert recorder.last_body["spaceKeys"][0]["filter"] == (
        "CAST(val('$.site') AS TEXT) = 'x\\' OR \\'1\\'=\\'1'"
    )


async def test_chunks_of_one_document_collapse_to_one_item(recorder, client):
    """NLWeb dedupes by URL downstream; returning a URL twice crowds out
    other documents."""
    events = ndjson_events("retrieve_site.ndjson")
    chunks = [e for e in events if "retrievedItem" in e]
    events.append(json.loads(json.dumps(chunks[0])))  # same document again
    recorder.route("POST", RETRIEVE, ndjson_response(events))
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    items = await p.search("noodle soup", SITE, num_results=10)
    assert len({i.url for i in items}) == len(items)


# ------------------------------------------------------------ statuses
async def test_a_broken_reranker_keeps_its_results(recorder, client):
    """Contract Q4a. Live, a bogus reranker makes the server send three
    status events and still return fallback chunks; 0.1.0 dropped all of
    them and reported success."""
    raw = ndjson_events("retrieve_broken_reranker.ndjson")
    assert sum("status" in e for e in raw) == 3
    p = make(recorder, client, "retrieve_broken_reranker.ndjson", reranker_id="bogus")
    items = await p.search("noodle soup", SITE, num_results=5)
    assert len(items) == 2


async def test_no_results_plus_a_problem_warns_and_does_not_raise(recorder, client):
    """Contract Q4b: NLWeb asked for results; an exception here would take
    down an ask request that could still answer from another endpoint."""
    recorder.route(
        "POST", RETRIEVE,
        ndjson_response([{"status": {"code": "NOT_FOUND", "message": "gone"}}]),
    )
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    with pytest.warns(UserWarning, match="NOT_FOUND"):
        assert await p.search("q", SITE, num_results=5) == []


async def test_feature_disabled_is_not_a_problem(recorder, client):
    """Contract Q1: the code alone decides."""
    events = ndjson_events("retrieve_site.ndjson")
    events.insert(0, {"status": {"code": "FEATURE_DISABLED", "message": "no llm",
                                 "details": {"feature": "reranking"}}})
    recorder.route("POST", RETRIEVE, ndjson_response(events))
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    import warnings as w
    with w.catch_warnings():
        w.simplefilter("error")          # any warning would fail this test
        assert len(await p.search("q", SITE, num_results=5)) == 2


async def test_an_empty_result_is_quiet(recorder, client):
    p = make(recorder, client, "retrieve_empty.ndjson")
    import warnings as w
    with w.catch_warnings():
        w.simplefilter("error")
        assert await p.search("q", "nope.example.com", num_results=5) == []


async def test_retrieval_makes_exactly_one_request(recorder, client):
    """0.1.0 polled up to 12 times by default, 63.8s measured live."""
    p = make(recorder, client, "retrieve_empty.ndjson")
    await p.search("q", SITE, num_results=5)
    assert len(recorder.requests) == 1


async def test_an_empty_query_never_reaches_the_server(recorder, client):
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    assert await p.search("   ", SITE) == []
    assert recorder.requests == []


# --------------------------------------------------------- schema mapping
def test_embedded_text_is_prose_not_json():
    """Embedding a JSON blob buries the words a query would match."""
    text, metadata = to_memory_fields(
        {"@type": "Recipe", "url": "https://x/1", "name": "Laksa",
         "description": "Coconut curry noodle soup."},
        site="s",
    )
    assert text == "Laksa\n\nCoconut curry noodle soup."
    assert "{" not in text
    assert metadata == {"url": "https://x/1", "site": "s", "name": "Laksa",
                        "schema_json": metadata["schema_json"]}
    assert json.loads(metadata["schema_json"])["@type"] == "Recipe"


def test_schema_without_text_fields_falls_back_to_json():
    """Retrieving nothing is worse than embedding a blob."""
    assert "@type" in schema_to_text({"@type": "Thing", "sameAs": "https://x"})


def test_a_document_without_a_url_is_refused():
    with pytest.raises(ValueError, match="needs a url"):
        to_memory_fields({"@type": "Thing", "name": "n"})


def test_nlweb_wrapper_shape_is_accepted():
    text, metadata = to_memory_fields(
        {"url": "https://x/2", "site": "s2",
         "schema_json": json.dumps({"@type": "Movie", "name": "Dune"})}
    )
    assert text == "Dune" and metadata["site"] == "s2"


# ----------------------------------------------------------------- spaces
def _space(name: str, space_id: str, embedder: str) -> dict[str, Any]:
    return {
        "spaceId": space_id, "name": name, "labels": {},
        "spaceEmbedders": [{"spaceId": space_id, "embedderId": embedder,
                            "defaultRetrievalWeight": 1.0, "createdAt": 1, "updatedAt": 1,
                            "createdById": "u", "updatedById": "u"}],
        "createdAt": 1, "updatedAt": 1, "ownerId": "o", "createdById": "u", "updatedById": "u",
        "defaultChunkingConfig": {"recursive": {"chunkSize": 256, "chunkOverlap": 25,
            "separators": [" "], "keepStrategy": "KEEP_END", "separatorIsRegex": False,
            "lengthMeasurement": "CHARACTER_COUNT"}},
    }


async def test_attach_by_name_refuses_a_different_embedder(recorder, client):
    recorder.route("GET", "/v1/spaces",
                   httpx.Response(200, json={"spaces": [_space("s", SPACE, "emb-1")], "nextToken": None}))
    p = GoodMemRetrievalProvider(space_name="s", embedder_id="emb-2", client=client)
    with pytest.raises(GoodMemSpaceError, match="not emb-2"):
        await p.search("q", SITE)


def test_a_provider_needs_somewhere_to_search():
    with pytest.raises(ValueError, match="space_id or space_name"):
        GoodMemRetrievalProvider()


# ----------------------------------------------------------------- lookup
async def test_object_lookup_returns_the_full_object(recorder, client):
    recorder.route("GET", f"/v1/spaces/{SPACE}/memories",
                   httpx.Response(200, json=load_json("memories_list.json")))
    lookup = GoodMemObjectLookupProvider(space_id=SPACE, client=client)
    obj = await lookup.get_by_id("https://films.org/m/dune")
    assert obj is not None
    assert obj["name"] and obj["@type"]


async def test_object_lookup_filters_by_url_server_side(recorder, client):
    recorder.route("GET", f"/v1/spaces/{SPACE}/memories",
                   httpx.Response(200, json={"memories": [], "nextToken": None}))
    lookup = GoodMemObjectLookupProvider(space_id=SPACE, client=client)
    assert await lookup.get_by_id("https://x/none") is None
    # the filter travels as a query parameter, URL-encoded on the wire
    sent = recorder.requests[-1].url.params["filter"]
    assert sent == "CAST(val('$.url') AS TEXT) = 'https://x/none'"


def test_fixtures_are_real_server_bytes():
    codes = [e["status"]["code"]
             for e in ndjson_events("retrieve_broken_reranker.ndjson") if "status" in e]
    assert "RERANKING_FAILED" in codes


# ------------------------------------------------ coverage gaps (2026-09-24)
async def test_an_unknown_status_code_is_surfaced_not_dropped(recorder, client, caplog):
    """Contract Q3: the SDK decodes a code it does not know as None. The
    items are kept, and the code is logged as UNKNOWN rather than vanishing."""
    import logging

    events = ndjson_events("retrieve_site.ndjson")
    events.insert(0, {"status": {"code": "A_CODE_FROM_THE_FUTURE", "message": "hello"}})
    recorder.route("POST", RETRIEVE, ndjson_response(events))
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    with caplog.at_level(logging.WARNING, logger="nlweb_goodmem.provider"):
        items = await p.search("noodle soup", SITE, num_results=5)
    assert len(items) == 2, "an unknown code must never discard results"
    assert "UNKNOWN" in caplog.text


async def test_server_order_is_preserved_and_no_threshold_is_sent(recorder, client):
    """RetrievedItem has no score; the only thing we owe NLWeb is the
    server's order, untouched, and never a relevance_threshold that would
    silently drop results on a reranker whose scale is not 0-1."""
    events = ndjson_events("retrieve_all.ndjson")
    expected = [
        e["retrievedItem"]["chunk"]["chunk"]["memoryId"] for e in events if "retrievedItem" in e
    ]
    memories = {e["memoryDefinition"]["memoryId"]: e["memoryDefinition"]["metadata"]["url"]
                for e in events if "memoryDefinition" in e}
    recorder.route("POST", RETRIEVE, ndjson_response(events))
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client, reranker_id="rr")
    items = await p.search("noodle soup", "all", num_results=10)
    assert [i.url for i in items] == [memories[m] for m in expected]
    assert "relevance_threshold" not in recorder.last_body["postProcessor"]["config"]


def test_filter_refuses_control_characters():
    """The live grammar rejects a raw newline inside a literal with a 400."""
    with pytest.raises(ValueError, match="control characters"):
        filters.text_equals("site", "a\nb")


def test_the_api_key_never_appears_in_a_repr():
    p = GoodMemRetrievalProvider(space_id=SPACE, base_url="https://x", api_key="gm_SECRET_VALUE")
    assert "gm_SECRET_VALUE" not in repr(p)
    assert "gm_SECRET_VALUE" not in repr(vars(p))
    assert "gm_SECRET_VALUE" not in repr(p._conn)


async def test_a_partial_batch_failure_is_reported_with_what_landed(recorder, client):
    """P38: the server answers HTTP 200 even when an item failed, so the
    per-item success flag is the only signal. The ids that did land are
    carried on the error so the caller can recover instead of retrying."""
    landed = load_json("memories_list.json")["memories"][0]
    recorder.route("POST", "/v1/memories:batchCreate", httpx.Response(200, json={
        "results": [
            {"memory": landed, "success": True},
            {"success": False, "error": {"code": 5, "message": "Space not found"}},
        ]
    }))
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    docs = [{"@type": "Thing", "url": "https://x/1", "name": "one"},
            {"@type": "Thing", "url": "https://x/2", "name": "two"}]
    with pytest.raises(GoodMemUploadError, match="1 of 2 documents failed") as excinfo:
        await upload_documents(p, docs, site="s", wait=False)
    assert excinfo.value.created_memory_ids == [landed["memoryId"]]


async def test_errors_propagate_with_the_servers_reason(recorder, client):
    """0.1.0 caught everything and returned an MDN link as a JSON string."""
    recorder.route("POST", RETRIEVE, httpx.Response(400, json={
        "errors": [{"field": "spaceKeys[0].spaceId", "message": "Invalid space ID format"}]
    }))
    p = GoodMemRetrievalProvider(space_id="not-a-uuid", client=client)
    with pytest.raises(Exception, match="Invalid space ID format"):
        await p.search("q", SITE)


async def test_an_ambiguous_space_name_is_an_error(recorder, client):
    recorder.route("GET", "/v1/spaces", httpx.Response(200, json={
        "spaces": [_space("s", SPACE, "e1"), _space("s", "other-id", "e1")], "nextToken": None}))
    p = GoodMemRetrievalProvider(space_name="s", client=client)
    with pytest.raises(GoodMemSpaceError, match="2 spaces are named"):
        await p.search("q", SITE)


async def test_get_sites_collects_the_distinct_values(recorder, client):
    recorder.route("GET", f"/v1/spaces/{SPACE}/memories",
                   httpx.Response(200, json=load_json("memories_list.json")))
    p = GoodMemRetrievalProvider(space_id=SPACE, client=client)
    assert set(await p.get_sites()) == {"recipes.example.com", "films.org"}
