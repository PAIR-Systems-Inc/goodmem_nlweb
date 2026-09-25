# Changelog

## 0.2.1

### Security

**An id is refused unless it is a UUID, before any request is made.** The
`goodmem` SDK (0.1.35) builds request paths by interpolating ids unencoded
(`f"/v1/spaces/{space_id}/memories"`, `f"/v1/memories/{id}"`) and httpx
resolves dot segments before it sends, so an id could steer a request to a
different endpoint. Every GoodMem id is a UUID, so one validator,
`nlweb_goodmem._ids.require_uuid`, accepts a canonical UUID (lowercased, or a
`uuid.UUID`) and refuses everything else with `ValueError`.

Measured against a local HTTP server that records each request line as it
arrives (`<U>` is a well-formed UUID):

| Input | 0.2.0 sent | 0.2.1 |
| --- | --- | --- |
| `space_id="../spaces/<U>"`, then `get_sites()` | `GET /v1/spaces/<U>/memories` — another space's memories | `ValueError` naming `space_id`; nothing sent |
| the same, then `GoodMemObjectLookupProvider.get_by_id(url)` | `GET /v1/spaces/<U>/memories?filter=…` | refused; nothing sent |
| `space_id="<U>#frag"`, then `get_sites()` | `GET /v1/spaces/<U>` — the space record, not its memories | refused; nothing sent |
| `space_id="<U>?x=1"`, then `get_by_id(url)` | `GET /v1/spaces/<U>?filter=…` | refused; nothing sent |
| `space_id="../spaces/<U>"`, then `search()` or `upload_documents()` | the value, in the request body | refused; nothing sent |
| a space id the server returns for `space_name` | used in the path unchecked | checked before every request |
| a memory id in a batch-create response, polled by `upload_documents(wait=True)` | `"../spaces/<U>"` became `GET /v1/spaces/<U>` | `GoodMemUploadError` carrying the created ids; no poll |
| `reranker_id` or `embedder_id` that is not a UUID | the value, in the request body | refused; nothing sent |

The same holds for `a/../../spaces/<U>`, `%2e%2e/spaces/<U>`,
`..%2Fspaces%2F<U>`, `<U>/../../spaces/<U>`, `""`, `" <U>"` and `"<U>\n"`.
Configured ids are checked when the provider is built, so a bad option fails
when NLWeb loads it; the space id is checked again right before each request.

`get_by_id(object_id)` is the entry point NLWeb feeds from a web request.
That id is the item's URL, not a GoodMem id, and it already travelled only
as an escaped literal inside the `filter` query parameter. It is not
required to be a UUID; tests now pin that every payload above, and
`<U>&max_results=500`, leaves the path at `/v1/spaces/<configured>/memories`
with exactly one query parameter.

### Fixed

- **The documented NLWeb configuration now loads.** The README, the package
  docstring and `GoodMemRetrievalProvider`'s docstring nested the options
  under `options:`. nlweb-core 0.7 passes every key beside `import_path` and
  `class_name` to the constructor, so the provider received one
  `options={...}` keyword and NLWeb's startup failed with `ValueError:
  GoodMemRetrievalProvider needs space_id or space_name`. The options now sit
  beside `class_name`.
- **The documented entry is named `default`.** It was `goodmem`, but
  nlweb-core's `ask` handler calls `get_retrieval_provider("default")`; with
  only a `goodmem` entry that raised `Retrieval provider 'default' is not
  configured`, and alongside another `default` it was loaded and never used.
- The README shows the `object_storage` entry for
  `GoodMemObjectLookupProvider` (NLWeb only enriches results when one named
  `default` exists), mentions `_env` keys such as `api_key_env`, and says that
  an empty `embedder_id`/`reranker_id` means "not set".

### Changed

- An empty `space_id` raises `space_id must be a UUID`. With `space_name`
  also set, 0.2.0 used the empty id instead of resolving the name. An empty
  `embedder_id` or `reranker_id` still means "not set".
- An uppercase id is sent lowercased, and a `uuid.UUID` is accepted (0.2.0's
  `search()` and `upload_documents()` raised on one).
- 159 offline tests (was 34): 119 in `tests/test_ids.py` drive the real SDK
  over a socket, and 6 in `tests/test_config.py` load the documented YAML
  with nlweb-core's own loader. Tests that used placeholder ids (`"emb-1"`, `"bogus"`,
  `"rr"`, `"not-a-uuid"`, `"other-id"`) now use UUIDs; the broken-reranker
  test uses the all-zero id its fixture was recorded with.

## 0.2.0

A rewrite. 0.1.0 shipped no NLWeb integration: it was fourteen classes
wrapping REST endpoints with `httpx`, and `nlweb` was never imported and was
not a dependency. It was the same template as `deepeval-goodmem` 0.1.0 — after
normalising names, `_client.py` differed by 16 lines out of 683.

0.2.0 implements NLWeb's actual provider interfaces, on the official `goodmem`
SDK.

### Added

- **`GoodMemRetrievalProvider`** — a real `nlweb_core.retriever.RetrievalProvider`.
  Registered purely by configuration (`import_path` / `class_name` / `options`),
  so it never has to be added to NLWeb's source tree.
- **Site filtering** — NLWeb's core requirement. Sites are memory metadata,
  filtered server-side with escaped expressions. One site, several sites
  (OR-ed), or `"all"`.
- **`GoodMemObjectLookupProvider`** — `ObjectLookupProvider.get_by_id(url)`,
  so NLWeb can enrich a result with the full Schema.org object.
- **`upload_documents()`** and the Schema.org mapping in `nlweb_goodmem._schema`.
- **`get_sites()`** — the distinct sites present in a space.
- 34 offline tests over captured server bytes and 13 live tests with verified
  teardown. 0.1.0 had 10 tests that passed against every defect below, and no
  workflow ever ran them.

### Fixed

Every defect was reproduced against a live server (v1.0.320) first.

| Behaviour | 0.1.0 | 0.2.0 |
| --- | --- | --- |
| Retrieval statuses | The NDJSON parser had branches for boundaries, memories, abstract replies and items — and **none for `status`**. A bogus reranker made the server send three status events; all were dropped and the call reported success | Classified per the retrieval status contract: informational notices ignored, real problems logged, unknown codes surfaced as `UNKNOWN` |
| A retrieval that failed outright | `success: true, totalResults: 0` with a message blaming indexing | Empty list plus a `UserWarning`; never raised into an `ask` request |
| Searching when nothing matches | Up to **12 re-POSTs** over 60s (`wait_for_indexing=True`, `poll_interval=5`), re-invoking the LLM each time when `llm_id` was set | One request |
| Relevance scores | Documented as "0-1"; live values are negative for vector and a different scale for reranker | Not exposed: `RetrievedItem` has no score field, and server order is preserved |
| `metadata_filter` | A raw string pasted into the request | Escaped; a site containing a quote matches nothing rather than everything |
| Any error | `run()` caught **everything** and returned `{"success": false, "error": "…check developer.mozilla.org"}` as a string, discarding the server's reason | Errors propagate with the server's message |
| `update_space(public_read=…)` | HTTP 400 — the field was removed from the API | Gone |
| Batch ingest | n/a | Per-item `success` is checked: the server returns HTTP 200 even when an item failed |
| API key | A plain attribute on every operation object | Held on a private connection |

### Migration

The fourteen `GoodMem*` operation classes are gone; they were a REST client,
and the `goodmem` SDK does that job better.

```python
# 0.1.0
from nlweb_goodmem import GoodMemRetrieveMemories
out = json.loads(GoodMemRetrieveMemories(...).run(query="…", space_ids=sid))

# 0.2.0 — through NLWeb
provider = GoodMemRetrievalProvider(space_name="nlweb", base_url=…, api_key=…)
items = await provider.search("…", "example.com")

# 0.2.0 — for plain API access, use the SDK
from goodmem import AsyncGoodmem
await AsyncGoodmem(base_url=…, api_key=…).spaces.create(...)
```

Python 3.9 is no longer supported; 3.10+ is required.

## 0.1.0

Initial release.
