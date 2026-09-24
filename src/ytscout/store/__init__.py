"""SQLite store. Only this package writes to the database.

``db`` opens the connection and applies migrations; ``repo`` holds the typed helpers.
Snapshot tables are append-only: no helper updates or deletes a snapshot row, and the
schema's triggers refuse it anyway.
"""

from ytscout.store.db import DB_FILENAME, connect, default_db_path, now_utc, to_utc_iso, utc_now

__all__ = ["DB_FILENAME", "connect", "default_db_path", "now_utc", "to_utc_iso", "utc_now"]
