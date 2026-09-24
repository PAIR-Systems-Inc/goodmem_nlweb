# Changelog

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
