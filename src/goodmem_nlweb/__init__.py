"""GoodMem as an NLWeb retrieval provider.

Registered by configuration -- NLWeb imports the class by path, so this
package never has to be added to NLWeb's own source tree::

    retrieval:
      default:
        import_path: goodmem_nlweb
        class_name: GoodMemRetrievalProvider
        base_url: https://localhost:8080
        api_key: gm_…
        space_name: nlweb

NLWeb speaks Schema.org and filters by *site*; GoodMem stores text and
metadata. ``goodmem_nlweb._schema`` documents the mapping.
"""

from goodmem_nlweb._connection import GoodMemConnection
from goodmem_nlweb._schema import schema_to_text, to_memory_fields
from goodmem_nlweb._spaces import GoodMemSpaceError
from goodmem_nlweb.provider import (
    GoodMemObjectLookupProvider,
    GoodMemRetrievalProvider,
    GoodMemUploadError,
    upload_documents,
)

__version__ = "0.3.0"

__all__ = [
    "GoodMemConnection",
    "GoodMemObjectLookupProvider",
    "GoodMemRetrievalProvider",
    "GoodMemSpaceError",
    "GoodMemUploadError",
    "__version__",
    "schema_to_text",
    "to_memory_fields",
    "upload_documents",
]
