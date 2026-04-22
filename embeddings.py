"""
Mirror Embeddings — shim for backwards compatibility.

The implementation has moved to kernel/embeddings.py.
All imports from this module continue to work unchanged.
"""
from kernel.embeddings import *  # noqa: F401, F403
from kernel.embeddings import get_embedding  # explicit re-export
