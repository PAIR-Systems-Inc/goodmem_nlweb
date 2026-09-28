"""GoodMem as an NLWeb retrieval provider.

Registered by configuration -- NLWeb imports the class by path, so this
package never has to be added to NLWeb's own source tree::

    retrieval:
      default:
        import_path: nlweb_goodmem
        class_name: GoodMemRetrievalProvider
        base_url: https://localhost:8080
        api_key: gm_…
        space_name: nlweb

NLWeb speaks Schema.org and filters by *site*; GoodMem stores text and
metadata. ``nlweb_goodmem._schema`` documents the mapping.
"""

from nlweb_goodmem._connection import GoodMemConnection
from nlweb_goodmem._schema import schema_to_text, to_memory_fields
from nlweb_goodmem._spaces import GoodMemSpaceError
from nlweb_goodmem.provider import (
    GoodMemObjectLookupProvider,
    GoodMemRetrievalProvider,
    GoodMemUploadError,
    upload_documents,
)

__version__ = "0.2.1"

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
