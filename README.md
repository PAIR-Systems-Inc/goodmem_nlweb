# nlweb-goodmem

GoodMem as an [NLWeb](https://github.com/nlweb-ai/NLWeb) retrieval provider.

```bash
pip install nlweb-goodmem
```

## Configure

NLWeb imports a provider by path, so this package never has to be added to
NLWeb's own source tree:

```yaml
retrieval:
  goodmem:
    import_path: nlweb_goodmem
    class_name: GoodMemRetrievalProvider
    options:
      base_url: https://localhost:8080
      api_key: gm_…
      space_name: nlweb
```

It implements `nlweb_core.retriever.RetrievalProvider`, so `search()` returns
real `RetrievedItem` objects and `close()` releases the client.

## Sites

NLWeb filters every call by *site*; GoodMem has no such concept. A site is
stored as memory metadata and filtered **server-side** with GoodMem's filter
grammar, the same way NLWeb's own Qdrant provider uses a `site` payload field.
Values are escaped, never interpolated — a site called `x' OR '1'='1` matches
nothing rather than everything.

```python
await provider.search("noodle soup", "recipes.example.com")   # one site
await provider.search("noodle soup", ["a.com", "b.com"])      # OR across sites
await provider.search("noodle soup", "all")                   # every site
await provider.search_all_sites("noodle soup")                # same thing
await provider.get_sites()                                    # ['a.com', 'b.com']
```

## Ingesting Schema.org documents

`RetrievalProvider` only reads, so ingestion is a helper rather than part of
the interface:

```python
from nlweb_goodmem import GoodMemRetrievalProvider, upload_documents

provider = GoodMemRetrievalProvider(space_name="nlweb", base_url=…, api_key=…)
await upload_documents(provider, [
    {"@type": "Recipe", "url": "https://ex.com/r/laksa", "name": "Singapore Laksa",
     "description": "A coconut curry noodle soup."},
], site="recipes.example.com")
```

| NLWeb | GoodMem |
| --- | --- |
| `url` | `metadata["url"]` — the item's identity, and what NLWeb dedupes on |
| `site` | `metadata["site"]` — what every search filters on |
| `raw_schema_object` | `metadata["schema_json"]` |
| (text to embed) | the memory's content |

The embedded text is **not** the raw JSON. Embedding a JSON blob buries the
words a query would match under punctuation and key names, so the text is
extracted from `name`, `headline`, `description`, `articleBody` and friends,
and the untouched object is kept alongside for NLWeb to return verbatim.

`upload_documents` inspects every item of the batch response: the server
returns HTTP 200 even when an item failed, so per-item `success` is the only
signal. A partial failure raises `GoodMemUploadError` carrying the ids that
did land.

## Looking objects up by URL

```python
from nlweb_goodmem import GoodMemObjectLookupProvider

lookup = GoodMemObjectLookupProvider(space_name="nlweb", base_url=…, api_key=…)
await lookup.get_by_id("https://ex.com/r/laksa")   # the full Schema.org object
```

Implements `ObjectLookupProvider`, so NLWeb can enrich a truncated search
result with the complete object without a second datastore.

## Scores, and why there are none

A GoodMem vector score is a negative inner product — the best match is the
*lowest* number — and a reranker score is a different scale that also goes
negative. `RetrievedItem` has no score field, and inventing one would imply a
comparability that does not hold. Results keep the server's ordering, which is
authoritative, and are never re-sorted here.

## Degraded retrieval

If the server reports a problem, whatever it did return is still returned and
the statuses are logged. If it reports a problem *and* returns nothing, the
result is an empty list plus a `UserWarning` — never an exception, because an
exception here would take down an `ask` request that could still answer from
another endpoint. Notices that carry no loss (`FEATURE_DISABLED`,
`LLM_CAPABILITY_INFERRED`) are ignored; a status code this version does not
know is reported as `UNKNOWN` rather than dropped.

## Which NLWeb?

This targets **`nlweb-core`** (the pip-installable package with the
config-driven provider architecture). The `nlweb-ai/NLWeb` reference
implementation has a different interface (`VectorDBClientInterface`, returning
`list[list[str]]`) and a hardcoded provider table, so a third-party package
cannot register with it — that one needs an upstream PR.

## Development

```bash
pip install -e ".[dev]"
ruff check src tests examples && mypy && pytest -m "not integration"
```

The offline suite replays NDJSON captured from a live GoodMem server
(v1.0.320) through the real SDK decoders. The live suite needs a server:

```bash
GOODMEM_BASE_URL=… GOODMEM_API_KEY=… GOODMEM_EMBEDDER_ID=… \
  GOODMEM_RERANKER_ID=… GOODMEM_VERIFY_SSL=0 \
  pytest -m integration
```

`GOODMEM_RERANKER_ID` is optional — the reranker test skips without it.
`GOODMEM_VERIFY_SSL=0` is for a local server with a self-signed certificate.

There is no default credential anywhere in this repository.

## License

MIT
