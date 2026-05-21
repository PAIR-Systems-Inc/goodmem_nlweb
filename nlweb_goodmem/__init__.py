"""NLWeb integration for GoodMem, the retrieval-augmented generation (RAG) memory backend for AI agents."""

from nlweb_goodmem._base import GoodMemOperation
from nlweb_goodmem._client import GoodMemClient
from nlweb_goodmem.create_memory import GoodMemCreateMemory
from nlweb_goodmem.create_space import GoodMemCreateSpace
from nlweb_goodmem.delete_memory import GoodMemDeleteMemory
from nlweb_goodmem.delete_space import GoodMemDeleteSpace
from nlweb_goodmem.get_memory import GoodMemGetMemory
from nlweb_goodmem.get_space import GoodMemGetSpace
from nlweb_goodmem.list_embedders import GoodMemListEmbedders
from nlweb_goodmem.list_memories import GoodMemListMemories
from nlweb_goodmem.list_spaces import GoodMemListSpaces
from nlweb_goodmem.retrieve_memories import GoodMemRetrieveMemories
from nlweb_goodmem.update_space import GoodMemUpdateSpace

__all__ = [
    "GoodMemClient",
    "GoodMemCreateMemory",
    "GoodMemCreateSpace",
    "GoodMemDeleteMemory",
    "GoodMemDeleteSpace",
    "GoodMemGetMemory",
    "GoodMemGetSpace",
    "GoodMemListEmbedders",
    "GoodMemListMemories",
    "GoodMemListSpaces",
    "GoodMemOperation",
    "GoodMemRetrieveMemories",
    "GoodMemUpdateSpace",
]
