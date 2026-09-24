"""GoodMem as an NLWeb retrieval provider."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
import warnings

from nlweb_core.retrieved_item import RetrievedItem
from nlweb_core.retriever import ObjectLookupProvider, RetrievalProvider

from nlweb_goodmem._connection import GoodMemConnection
from nlweb_goodmem._results import classify, hits_from_events
from nlweb_goodmem._schema import to_memory_fields
from nlweb_goodmem._spaces import GoodMemSpaceError, resolve
from nlweb_goodmem.filters import combine, from_mapping, text_equals

if TYPE_CHECKING:
    from collections.abc import Sequence


logger = logging.getLogger(__name__)

#: Listing is bounded: a site with more documents than this needs a filter,
#: not a bigger page.
MAX_LISTED = 1000


class GoodMemRetrievalProvider(RetrievalProvider):
    """Serve NLWeb retrieval from a GoodMem space.

    Registered by configuration -- NLWeb imports the class by path, so this
    package never has to be added to NLWeb's own source tree::

        retrieval:
          goodmem:
            import_path: nlweb_goodmem
            class_name: GoodMemRetrievalProvider
            options:
              base_url: https://localhost:8080
              api_key: gm_…
              space_name: nlweb

    NLWeb filters every call by *site*, which GoodMem has no concept of, so a
    site is stored as memory metadata and filtered server-side with GoodMem's
    filter grammar. Values are escaped, not interpolated. This is how NLWeb's
    own Qdrant provider does it (a ``site`` field in the payload).

    Results keep the server's ordering. A GoodMem vector score is a negative
    inner product and a reranker score is a different scale, so neither is
    exposed as an NLWeb relevance number -- ``RetrievedItem`` has no score
    field, and inventing one would imply a comparability that does not hold.

    A retrieval the server reports a problem with still returns whatever it
    did return, and logs the statuses. A retrieval that reported a problem
    and returned nothing returns an empty list and warns: NLWeb asks for
    results, and an exception here would take down an ``ask`` request that
    could still answer from another endpoint. (Retrieval status contract,
    Q4a/Q4b.)
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        space_id: str | None = None,
        space_name: str | None = None,
        embedder_id: str | None = None,
        create_space: bool = False,
        reranker_id: str | None = None,
        fetch_k: int | None = None,
        site_field: str = "site",
        url_field: str = "url",
        verify_ssl: bool | str = True,
        timeout: float = 60.0,
        client: Any = None,
        **_ignored: Any,
    ) -> None:
        if not (space_id or space_name):
            raise ValueError(
                "GoodMemRetrievalProvider needs space_id or space_name in its "
                "options so it knows what to search."
            )
        self._conn = GoodMemConnection(
            base_url=base_url,
            api_key=api_key,
            verify_ssl=verify_ssl,
            timeout=timeout,
            client=client,
        )
        self.space_id = space_id
        self.space_name = space_name
        self.embedder_id = embedder_id
        self.create_space = create_space
        self.reranker_id = reranker_id
        self.fetch_k = fetch_k
        self.site_field = site_field
        self.url_field = url_field
        self._resolved_space_id: str | None = space_id

    # ------------------------------------------------------------ internals
    async def _space(self) -> str:
        if self._resolved_space_id is None:
            self._resolved_space_id = await resolve(
                self._conn.client(),
                name=self.space_name or "",
                embedder_id=self.embedder_id,
                create=self.create_space,
            )
        return self._resolved_space_id

    def _site_filter(self, site: str | Sequence[str] | None) -> str | None:
        """Build the server-side expression that scopes a search to sites.

        NLWeb passes ``"all"`` (or an empty value) to mean every site.
        """
        if site is None:
            return None
        sites = [site] if isinstance(site, str) else [s for s in site if s]
        sites = [s for s in sites if s and s.lower() not in ("all", "*")]
        if not sites:
            return None
        clauses = [text_equals(self.site_field, s) for s in sites]
        if len(clauses) == 1:
            return clauses[0]
        return " OR ".join(f"({c})" for c in clauses)

    async def _retrieve(
        self, query: str, expression: str | None, num_results: int
    ) -> list[dict[str, Any]]:
        client = self._conn.client()
        space_id = await self._space()
        kwargs: dict[str, Any] = {
            "message": query,
            "requested_size": self.fetch_k or num_results,
            "fetch_memory": True,
            "stream": False,
        }
        if expression is None:
            kwargs["space_ids"] = [space_id]
        else:
            kwargs["space_keys"] = [{"spaceId": space_id, "filter": expression}]
        if self.reranker_id:
            kwargs["reranker_id"] = self.reranker_id
            kwargs["max_results"] = num_results

        events = list(await client.memories.retrieve(**kwargs))
        statuses, degraded = classify(events)
        hits = hits_from_events(events, reranked=bool(self.reranker_id))

        if degraded:
            summary = "; ".join(
                f"{s.get('code', 'UNKNOWN')}: {s.get('message', '')}" for s in statuses
            )
            logger.warning(
                "GoodMem retrieval was degraded (%d result(s) returned): %s",
                len(hits),
                summary,
            )
            if not hits:
                # Contract Q4b: NLWeb asked for results and got none because
                # something broke. Returning [] silently would read as "this
                # site has nothing to say", so the failure is surfaced here
                # rather than raised into the caller's ask request.
                warnings.warn(
                    f"GoodMem retrieval returned nothing and reported a problem: {summary}",
                    stacklevel=3,
                )
        return hits[:num_results]

    def _to_items(self, hits: list[dict[str, Any]]) -> list[RetrievedItem]:
        """Build RetrievedItems, one per document, in the server's order.

        Several chunks of one document are one NLWeb item: NLWeb dedupes by
        URL downstream, and returning the same URL repeatedly would crowd out
        other documents.
        """
        items: list[RetrievedItem] = []
        seen: set[str] = set()
        for hit in hits:
            metadata = hit.get("metadata") or {}
            url = metadata.get(self.url_field) or hit.get("memory_id") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            items.append(
                RetrievedItem(
                    url=url,
                    raw_schema_object=metadata.get("schema_json")
                    or hit.get("chunk_text", ""),
                    site=metadata.get(self.site_field, ""),
                )
            )
        return items

    # -------------------------------------------------- RetrievalProvider
    async def search(
        self,
        query: str,
        site: str | list[str],
        num_results: int = 50,
        **kwargs: Any,
    ) -> list[RetrievedItem]:
        """Search one or more sites. Returns items in the server's order."""
        if not query or not query.strip():
            return []
        expression = combine(
            self._site_filter(site),
            from_mapping(kwargs["metadata_filter"])
            if kwargs.get("metadata_filter")
            else None,
        )
        return self._to_items(await self._retrieve(query, expression, num_results))

    async def close(self) -> None:
        """Close the SDK client when this provider owns it."""
        await self._conn.close()

    # ------------------------------------------------------- extra surface
    async def search_all_sites(
        self, query: str, num_results: int = 50, **kwargs: Any
    ) -> list[RetrievedItem]:
        """Search every site in the space."""
        return await self.search(query, "all", num_results, **kwargs)

    async def get_sites(self) -> list[str]:
        """Distinct site values present in the space.

        GoodMem has no aggregation, so this lists memories and collects the
        field. It is bounded by ``MAX_LISTED``; a space larger than that
        should be queried by site rather than enumerated.
        """
        client = self._conn.client()
        page = await client.memories.list(
            space_id=await self._space(), max_items=MAX_LISTED
        )
        sites: list[str] = []
        async for memory in page:
            value = (getattr(memory, "metadata", None) or {}).get(self.site_field)
            if value and value not in sites:
                sites.append(value)
        return sites


class GoodMemObjectLookupProvider(ObjectLookupProvider):
    """Fetch a full Schema.org object by its URL.

    NLWeb uses this to replace a truncated search result with the complete
    object. Backed by a GoodMem metadata filter on the URL field, so it needs
    no second store.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._provider = GoodMemRetrievalProvider(**kwargs)

    async def get_by_id(self, object_id: str) -> dict[str, Any] | None:
        """Return the object stored under ``object_id``, or None."""
        import json

        client = self._provider._conn.client()
        space_id = await self._provider._space()
        expression = text_equals(self._provider.url_field, object_id)
        page = await client.memories.list(
            space_id=space_id, filter=expression, max_items=1
        )
        async for memory in page:
            raw = (getattr(memory, "metadata", None) or {}).get("schema_json")
            if not raw:
                return None
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                return None
            return parsed[0] if isinstance(parsed, list) and parsed else parsed
        return None

    async def close(self) -> None:
        await self._provider.close()


async def upload_documents(
    provider: GoodMemRetrievalProvider,
    documents: Sequence[Any],
    *,
    site: str = "",
    wait: bool = True,
    timeout: float = 180.0,
) -> list[str]:
    """Ingest Schema.org documents into the provider's space.

    Not part of ``RetrievalProvider`` -- NLWeb's pluggable interface only
    reads -- but a provider is useless without a way to fill it. Returns the
    created memory ids.
    """
    import asyncio
    import time

    from goodmem import MemoryCreationRequest

    client = provider._conn.client()
    space_id = await provider._space()
    requests = []
    for document in documents:
        text, metadata = to_memory_fields(document, site=site)
        # model_validate with the wire aliases: the model sets
        # populate_by_name, so snake_case also works at runtime, but only the
        # aliases are what the server actually receives.
        requests.append(
            MemoryCreationRequest.model_validate(
                {
                    "spaceId": space_id,
                    "originalContent": text,
                    "contentType": "text/plain",
                    "metadata": metadata,
                }
            )
        )
    if not requests:
        return []

    response = await client.memories.batch_create(requests=requests)
    accepted: list[str] = []
    failures: list[str] = []
    for position, result in enumerate(response.results or []):
        # On success the id is on the nested memory; the result's own
        # memory_id stays null. The HTTP status is 200 even when an item
        # failed, so per-item success is the only signal (P38).
        memory_id = result.memory_id or getattr(result.memory, "memory_id", None)
        if result.success and memory_id:
            accepted.append(memory_id)
        else:
            failures.append(f"#{position}: {result.error or 'unknown error'}")
    if failures:
        raise GoodMemUploadError(
            f"{len(failures)} of {len(requests)} documents failed: "
            + "; ".join(failures),
            created_memory_ids=accepted,
        )

    if wait:
        deadline = time.monotonic() + timeout
        pending = set(accepted)
        while pending and time.monotonic() < deadline:
            for memory_id in list(pending):
                memory = await client.memories.get(id=memory_id)
                if memory.processing_status == "COMPLETED":
                    pending.discard(memory_id)
                elif memory.processing_status not in ("PENDING", "PROCESSING"):
                    raise GoodMemUploadError(
                        f"Memory {memory_id} failed to index: "
                        f"{memory.processing_status}",
                        created_memory_ids=accepted,
                    )
            if pending:
                await asyncio.sleep(2)
        if pending:
            raise GoodMemUploadError(
                f"{len(pending)} document(s) were written but did not finish "
                f"indexing within {timeout:g}s",
                created_memory_ids=accepted,
            )
    return accepted


class GoodMemUploadError(RuntimeError):
    """An ingest failed partway. ``created_memory_ids`` records what landed."""

    def __init__(
        self, message: str, *, created_memory_ids: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.created_memory_ids = created_memory_ids or []


__all__ = [
    "GoodMemObjectLookupProvider",
    "GoodMemRetrievalProvider",
    "GoodMemSpaceError",
    "GoodMemUploadError",
    "upload_documents",
]
