"""
Mirror DB — shim for backwards compatibility.

The implementation has moved to kernel/db.py.
All imports from this module continue to work unchanged.
"""
from kernel.db import *  # noqa: F401, F403
from kernel.db import get_db, LocalDB, SupabaseDB, QueryResponse  # explicit re-export
try:
    from kernel.db_sqlite import SQLiteDB  # noqa: F401 — available when sqlite-vec installed
except ImportError:
    pass
