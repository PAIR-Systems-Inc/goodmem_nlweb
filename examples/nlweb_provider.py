"""Ingest Schema.org documents into GoodMem and serve them to NLWeb.

    GOODMEM_BASE_URL=… GOODMEM_API_KEY=… GOODMEM_EMBEDDER_ID=… \
    python examples/nlweb_provider.py
"""

from __future__ import annotations

import asyncio
import os

from nlweb_goodmem import GoodMemRetrievalProvider, upload_documents

DOCS = [
    {
        "@type": "Recipe",
        "url": "https://example.com/recipes/laksa",
        "name": "Singapore Laksa",
        "description": "A coconut curry noodle soup with prawns and tofu puffs.",
    },
    {
        "@type": "Recipe",
        "url": "https://example.com/recipes/pho",
        "name": "Beef Pho",
        "description": "Vietnamese rice noodle soup with charred ginger and star anise.",
    },
]
SITE = "example.com"


async def main() -> None:
    provider = GoodMemRetrievalProvider(
        base_url=os.environ["GOODMEM_BASE_URL"],
        api_key=os.environ["GOODMEM_API_KEY"],
        space_name=os.getenv("GOODMEM_SPACE_NAME", "nlweb-demo"),
        embedder_id=os.environ["GOODMEM_EMBEDDER_ID"],
        create_space=True,
        verify_ssl=os.getenv("GOODMEM_VERIFY_SSL", "1") not in ("0", "false"),
    )
    try:
        created = await upload_documents(provider, DOCS, site=SITE)
        print(f"ingested {len(created)} document(s) for site {SITE}")

        for item in await provider.search("coconut noodle soup", SITE, num_results=5):
            schema = item.schema_object[0]
            print(f"  {schema.get('name')}  <{item.url}>  site={item.site}")

        print("sites in the space:", await provider.get_sites())
    finally:
        await provider.close()


asyncio.run(main())
