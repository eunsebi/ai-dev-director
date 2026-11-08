"""
Canonical DB path resolver for AI DEV DIRECTOR.

Every component (StateMachine, ChatManager, ProviderManager, dashboard) must obtain the
SQLite path through get_db_path(). The path is absolute and anchored at the project source
root, so running the dashboard or CLI from a different cwd can never create a split-brain DB.

Test isolation: when DIRECTOR_TESTING=1 and TEST_DB_PATH is set, that path is returned instead.
"""
import os
import sqlite3
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = SOURCE_ROOT / "data" / "director.db"


def get_db_path() -> Path:
    if os.environ.get("DIRECTOR_TESTING") == "1" and os.environ.get("TEST_DB_PATH"):
        return Path(os.environ["TEST_DB_PATH"]).resolve()
    return DEFAULT_DB_PATH


def get_lock_dir() -> Path:
    """Directory for cross-process lock files (next to the DB; isolated under TEST_DB_PATH in tests)."""
    return get_db_path().parent / "locks"


class ClosingConnection(sqlite3.Connection):
    """`with connect(...) as conn:` commits (or rolls back) AND closes.

    The stock sqlite3 context manager only ends the transaction. On Python 3.14 a connection sits in a reference
    cycle, so it is freed (and its file descriptor closed) only when the cyclic GC happens to run. The long-running
    dashboard/companion server opened one per request and hit the 256-descriptor limit (247 open handles to
    director.db) after about 12 hours; it then could not open index.html and answered with empty replies."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(path=None, **kwargs) -> sqlite3.Connection:
    """sqlite3.connect for Director's DB with a closing context manager and Row results."""
    conn = sqlite3.connect(str(path or get_db_path()), factory=ClosingConnection, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn
