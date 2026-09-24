"""Connection, migrations and the one clock.

``connect(path)`` creates the parent folder, opens the database in WAL mode with foreign
keys on and ``sqlite3.Row`` rows, then applies every ``migrations/NNNN_*.sql`` not yet
recorded in ``schema_migrations``, in order, each in its own transaction. Calling it again
applies nothing.

All timestamps are ISO-8601 UTC text (``YYYY-MM-DDTHH:MM:SSZ``). ``now_utc()`` is the only
place in the package that reads the clock.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ytscout.settings import Settings

DB_FILENAME = "ytscout.sqlite"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[A-Za-z0-9_]+\.sql$")
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class MigrationError(Exception):
    """A migration file is misnamed or failed to apply."""


def now_utc() -> str:
    """The current time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return to_utc_iso(datetime.now(UTC))


def to_utc_iso(value: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ``. Naive datetimes are refused."""
    if value.tzinfo is None:
        raise ValueError("naive datetime: pass an aware datetime (UTC)")
    return value.astimezone(UTC).strftime(_ISO_FORMAT)


def default_db_path(settings: Settings) -> Path:
    """``<data_dir>/ytscout.sqlite``."""
    return settings.data_dir / DB_FILENAME


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """``(version, path)`` for every ``NNNN_*.sql`` in ``directory``, lowest version first."""
    found: dict[int, Path] = {}
    for path in directory.glob("*.sql"):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise MigrationError(f"migration file not named NNNN_name.sql: {path.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"two migrations share version {version}: {path.name}")
        found[version] = path
    return sorted(found.items())


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in order; return the versions applied (empty if none)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    done = applied_versions(conn)
    applied: list[int] = []
    for version, path in migration_files(directory):
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        record = (
            f"INSERT INTO schema_migrations (version, applied_at) "
            f"VALUES ({version}, '{now_utc()}');"
        )
        try:
            conn.executescript(f"BEGIN;\n{sql}\n{record}\nCOMMIT;")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.rollback()
            raise MigrationError(f"migration {path.name} failed: {exc}") from exc
        applied.append(version)
    return applied


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database at ``path`` and bring its schema up to date."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn
