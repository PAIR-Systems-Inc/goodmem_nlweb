"""Ids that reach a URL path must be UUIDs, and are refused before any request.

The goodmem SDK (0.1.35) builds paths by interpolating ids unencoded --
``f"/v1/spaces/{space_id}/memories"``, ``f"/v1/memories/{id}"`` -- and httpx
resolves dot segments before it sends. On 0.2.0, a ``space_id`` of
``"<U>#frag"`` made ``get_sites()`` send ``GET /v1/spaces/<U>`` and one of
``"../spaces/<U>"`` listed a different space's memories.

These tests drive the package's own client construction, the real SDK and
real httpx over a socket to a local HTTP server that records every request
line exactly as it arrived. A refusal counts only if that server saw nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest

from nlweb_goodmem import (
    GoodMemObjectLookupProvider,
    GoodMemRetrievalProvider,
    GoodMemUploadError,
    filters,
    upload_documents,
)
from tests.conftest import load_json

U = "0b5e1c2a-6f3d-4e8b-9a7c-1d2e3f405162"
SPACE = load_json("space.json")["spaceId"]
EMBEDDER = "01a0d0f1-1111-7222-8333-944455556666"
KEY = "gm_test_key_not_a_real_credential"
SITE = "recipes.example.com"
DOC = {"@type": "Thing", "url": "https://x/1", "name": "one"}

#: Every one of these must be refused wherever an id can reach a path.
PAYLOADS = [
    f"../spaces/{U}",
    f"a/../../spaces/{U}",
    f"%2e%2e/spaces/{U}",
    f"..%2Fspaces%2F{U}",
    f"{U}/../../spaces/{U}",
    "",
    f" {U}",
    f"{U}?x=1",
    f"{U}#frag",
    f"{U}\n",  # `$` in a Python regex matches before a trailing newline
]


# ------------------------------------------------------ recording server
class Seen(NamedTuple):
    method: str
    target: str  # the request-target, exactly as it arrived on the wire
    body: bytes

    @property
    def path(self) -> str:
        return self.target.split("?", 1)[0]

    def json(self) -> Any:
        return json.loads(self.body)


class RecordingServer:
    def __init__(self) -> None:
        self.seen: list[Seen] = []
        self.routes: dict[tuple[str, str], tuple[int, str, bytes]] = {}
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                recorder.seen.append(Seen(self.command, self.path, body))
                status, content_type, payload = recorder.routes.get(
                    (self.command, self.path.split("?", 1)[0]),
                    (404, "application/json", b'{"message": "no such route"}'),
                )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

            def log_message(self, *args: Any) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def route(self, method: str, path: str, body: Any, *, ndjson: bool = False) -> None:
        if ndjson:
            raw = "".join(json.dumps(event) + "\n" for event in body).encode()
            self.routes[(method, path)] = (200, "application/x-ndjson", raw)
        else:
            self.routes[(method, path)] = (
                200,
                "application/json",
                json.dumps(body).encode(),
            )

    def reset(self) -> None:
        self.seen.clear()
        self.routes.clear()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture(scope="module")
def _server() -> Iterator[RecordingServer]:
    server = RecordingServer()
    yield server
    server.stop()


@pytest.fixture
def server(_server: RecordingServer) -> RecordingServer:
    _server.reset()
    return _server


# --------------------------------------------------------------- helpers
def retrieval(server: RecordingServer, **options: Any) -> GoodMemRetrievalProvider:
    """A provider that owns its client, built the way NLWeb builds it."""
    return GoodMemRetrievalProvider(base_url=server.url, api_key=KEY, **options)


def lookup(server: RecordingServer, **options: Any) -> GoodMemObjectLookupProvider:
    return GoodMemObjectLookupProvider(base_url=server.url, api_key=KEY, **options)


async def using(provider: Any, call: Callable[[Any], Awaitable[Any]]) -> Any:
    try:
        return await call(provider)
    finally:
        await provider.close()


async def attempt(call: Callable[[], Awaitable[Any]]) -> BaseException | None:
    """Run an entry point and hand back what it raised, if anything.

    Deliberately blind: on code without the check, what escapes is an SDK
    or httpx error, and the test must still get to report what was sent.
    """
    try:
        await call()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


def assert_refused(error: BaseException | None, field: str) -> None:
    assert isinstance(error, ValueError), f"expected a refusal, got {error!r}"
    assert field in str(error) and "must be a UUID" in str(error), str(error)


#: Every public call that puts the configured space id into a request.
#: Construction is inside each call, so a refusal at load time counts too.
SPACE_ENTRY_POINTS: dict[str, Callable[[RecordingServer, str], Awaitable[Any]]] = {
    "search": lambda s, sid: using(
        retrieval(s, space_id=sid), lambda p: p.search("noodle soup", SITE)
    ),
    "search_all_sites": lambda s, sid: using(
        retrieval(s, space_id=sid), lambda p: p.search_all_sites("noodle soup")
    ),
    "get_sites": lambda s, sid: using(
        retrieval(s, space_id=sid), lambda p: p.get_sites()
    ),
    "upload_documents": lambda s, sid: using(
        retrieval(s, space_id=sid),
        lambda p: upload_documents(p, [DOC], site=SITE, wait=False),
    ),
    "ObjectLookupProvider.get_by_id": lambda s, sid: using(
        lookup(s, space_id=sid), lambda p: p.get_by_id("https://films.org/m/dune")
    ),
}


# ---------------------------------------------------- the space id (config)
@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("entry", list(SPACE_ENTRY_POINTS))
async def test_a_configured_space_id_that_is_not_a_uuid_never_leaves(
    server: RecordingServer, entry: str, payload: str
) -> None:
    error = await attempt(lambda: SPACE_ENTRY_POINTS[entry](server, payload))
    assert server.seen == [], f"the server received {[s[:2] for s in server.seen]}"
    assert_refused(error, "space_id")


@pytest.mark.parametrize("payload", PAYLOADS)
async def test_a_space_id_the_server_resolves_by_name_is_checked_too(
    server: RecordingServer, payload: str
) -> None:
    """The space id is checked right before every request, not only when it
    was configured: one resolved from ``space_name`` is in the path as well."""
    server.route(
        "GET", "/v1/spaces", {"spaces": [_space("nlweb", payload)], "nextToken": None}
    )
    error = await attempt(
        lambda: using(retrieval(server, space_name="nlweb"), lambda p: p.get_sites())
    )
    assert [s.path for s in server.seen] == ["/v1/spaces"], (
        f"the server received {[s[:2] for s in server.seen]}"
    )
    assert_refused(error, "space_id")


# ------------------------------------------- ids that only travel in a body
@pytest.mark.parametrize("payload", [p for p in PAYLOADS if p])
async def test_a_reranker_id_that_is_not_a_uuid_is_refused(
    server: RecordingServer, payload: str
) -> None:
    """Body only, so not a traversal -- checked because the helper is at hand.
    An empty value still means "no reranker", as it always has."""
    error = await attempt(
        lambda: using(
            retrieval(server, space_id=SPACE, reranker_id=payload),
            lambda p: p.search("noodle soup", SITE),
        )
    )
    assert server.seen == [], f"the server received {[s[:2] for s in server.seen]}"
    assert_refused(error, "reranker_id")


@pytest.mark.parametrize("payload", [p for p in PAYLOADS if p])
async def test_an_embedder_id_that_is_not_a_uuid_is_refused(
    server: RecordingServer, payload: str
) -> None:
    server.route("GET", "/v1/spaces", {"spaces": [], "nextToken": None})
    error = await attempt(
        lambda: using(
            retrieval(
                server, space_name="nlweb", embedder_id=payload, create_space=True
            ),
            lambda p: p.search("noodle soup", SITE),
        )
    )
    assert server.seen == [], f"the server received {[s[:2] for s in server.seen]}"
    assert_refused(error, "embedder_id")


# ------------------------------------------- memory ids the server issued
@pytest.mark.parametrize("payload", PAYLOADS)
async def test_a_memory_id_from_a_batch_response_is_checked_before_polling(
    server: RecordingServer, payload: str
) -> None:
    """``upload_documents(wait=True)`` polls ``GET /v1/memories/{id}`` with ids
    the server handed back. Nothing outside the process chooses them, but they
    reach a path, so they get the same check, and the ids that did land are
    still reported."""
    server.route(
        "POST",
        "/v1/memories:batchCreate",
        {"results": [{"success": True, "memoryId": payload}]},
    )
    error = await attempt(
        lambda: using(
            retrieval(server, space_id=SPACE),
            lambda p: upload_documents(p, [DOC], site=SITE, wait=True, timeout=5),
        )
    )
    assert [s.path for s in server.seen] == ["/v1/memories:batchCreate"], (
        f"the server received {[s[:2] for s in server.seen]}"
    )
    assert isinstance(error, GoodMemUploadError), repr(error)
    if payload:  # an empty id is already reported as a failed item
        assert "memory_id must be a UUID" in str(error)
        assert error.created_memory_ids == [payload]


# -------------------------------------- the object id (NLWeb web request)
@pytest.mark.parametrize(
    "object_id", [*PAYLOADS, f"{U}&max_results=500", "https://x/a b"]
)
async def test_a_lookup_id_from_a_web_request_never_reaches_the_path(
    server: RecordingServer, object_id: str
) -> None:
    """``get_by_id`` is what NLWeb calls with an id taken from a web request.

    That id is the item's URL, not a GoodMem id, so it is not a UUID and is
    not refused. It travels only as an escaped literal inside the ``filter``
    query parameter: the path stays the configured space's, and no extra
    query parameter can be smuggled in. A control character, which the
    filter grammar cannot carry, is refused by the filter builder instead.
    """
    server.route(
        "GET", f"/v1/spaces/{SPACE}/memories", {"memories": [], "nextToken": None}
    )
    if "\n" in object_id:
        error = await attempt(
            lambda: using(
                lookup(server, space_id=SPACE), lambda p: p.get_by_id(object_id)
            )
        )
        assert isinstance(error, ValueError) and "control characters" in str(error)
        assert server.seen == []
        return
    found = await using(
        lookup(server, space_id=SPACE), lambda p: p.get_by_id(object_id)
    )
    assert found is None
    assert [(s.method, s.path) for s in server.seen] == [
        ("GET", f"/v1/spaces/{SPACE}/memories")
    ]
    query = parse_qs(urlsplit(server.seen[0].target).query, keep_blank_values=True)
    assert query == {"filter": [filters.text_equals("url", object_id)]}


# ------------------------------------------------- a valid id still works
VALID_PATHS: dict[str, tuple[str, str]] = {
    "search": ("POST", "/v1/memories:retrieve"),
    "search_all_sites": ("POST", "/v1/memories:retrieve"),
    "get_sites": ("GET", f"/v1/spaces/{U}/memories"),
    "upload_documents": ("POST", "/v1/memories:batchCreate"),
    "ObjectLookupProvider.get_by_id": ("GET", f"/v1/spaces/{U}/memories"),
}


@pytest.mark.parametrize(
    "given", [U, U.upper(), uuid.UUID(U)], ids=["lower", "upper", "UUID"]
)
@pytest.mark.parametrize("entry", list(SPACE_ENTRY_POINTS))
async def test_a_valid_space_id_reaches_exactly_the_intended_request(
    server: RecordingServer, entry: str, given: Any
) -> None:
    server.route("POST", "/v1/memories:retrieve", [], ndjson=True)
    server.route("GET", f"/v1/spaces/{U}/memories", {"memories": [], "nextToken": None})
    server.route(
        "POST",
        "/v1/memories:batchCreate",
        {"results": [{"success": True, "memoryId": EMBEDDER}]},
    )
    await SPACE_ENTRY_POINTS[entry](server, given)
    assert [(s.method, s.path) for s in server.seen] == [VALID_PATHS[entry]]
    sent = server.seen[0]
    if sent.method == "POST":  # the id is in the body, lowercased
        assert U in sent.body.decode() and U.upper() not in sent.body.decode()


async def test_a_valid_memory_id_is_polled_at_its_own_path(
    server: RecordingServer,
) -> None:
    memory = load_json("memories_list.json")["memories"][0]
    server.route(
        "POST",
        "/v1/memories:batchCreate",
        {"results": [{"success": True, "memory": memory}]},
    )
    server.route("GET", f"/v1/memories/{memory['memoryId']}", memory)
    created = await using(
        retrieval(server, space_id=SPACE),
        lambda p: upload_documents(p, [DOC], site=SITE, wait=True, timeout=5),
    )
    assert created == [memory["memoryId"]]
    assert [(s.method, s.path) for s in server.seen] == [
        ("POST", "/v1/memories:batchCreate"),
        ("GET", f"/v1/memories/{memory['memoryId']}"),
    ]


async def test_a_valid_reranker_id_is_sent_and_an_empty_one_means_none(
    server: RecordingServer,
) -> None:
    server.route("POST", "/v1/memories:retrieve", [], ndjson=True)
    await using(
        retrieval(server, space_id=SPACE, reranker_id=U.upper()),
        lambda p: p.search("noodle soup", SITE),
    )
    await using(
        retrieval(server, space_id=SPACE, reranker_id=""),
        lambda p: p.search("noodle soup", SITE),
    )
    with_reranker, without = (s.json() for s in server.seen)
    assert U in json.dumps(with_reranker["postProcessor"])
    assert "postProcessor" not in without


# ------------------------------------------------------------ the helper
def test_require_uuid_accepts_only_the_canonical_form() -> None:
    from nlweb_goodmem._ids import require_uuid

    assert require_uuid(U.upper(), "space_id") == U
    assert require_uuid(uuid.UUID(U), "space_id") == U
    # uuid.UUID() itself accepts every one of these; a path segment must not.
    for loose in (
        U.replace("-", ""),
        "{" + U + "}",
        "urn:uuid:" + U,
        f"{U}\n",
        None,
        7,
    ):
        with pytest.raises(ValueError, match="space_id must be a UUID"):
            require_uuid(loose, "space_id")


def test_a_refusal_does_not_echo_the_value() -> None:
    """A mistyped option may be a credential; it must not land in a log."""
    from nlweb_goodmem._ids import require_uuid

    with pytest.raises(ValueError) as excinfo:
        require_uuid("gm_SECRET_VALUE", "space_id")
    assert "gm_SECRET_VALUE" not in str(excinfo.value)


def _space(name: str, space_id: str) -> dict[str, Any]:
    return {
        "spaceId": space_id,
        "name": name,
        "labels": {},
        "spaceEmbedders": [
            {
                "spaceId": space_id,
                "embedderId": EMBEDDER,
                "defaultRetrievalWeight": 1.0,
                "createdAt": 1,
                "updatedAt": 1,
                "createdById": U,
                "updatedById": U,
            }
        ],
        "createdAt": 1,
        "updatedAt": 1,
        "ownerId": U,
        "createdById": U,
        "updatedById": U,
        "defaultChunkingConfig": {
            "recursive": {
                "chunkSize": 256,
                "chunkOverlap": 25,
                "separators": [" "],
                "keepStrategy": "KEEP_END",
                "separatorIsRegex": False,
                "lengthMeasurement": "CHARACTER_COUNT",
            }
        },
    }
