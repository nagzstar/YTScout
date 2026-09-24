"""Store: schema, migrations, round-trips and the append-only rule."""

from __future__ import annotations

import inspect
import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ytscout.store import db, repo
from ytscout.store.db import MigrationError, connect, migrate, now_utc, to_utc_iso

EXPECTED_TABLES = {
    "schema_migrations",
    "channels",
    "channel_snapshots",
    "videos",
    "video_snapshots",
    "transcripts",
    "own_analytics",
    "own_daily",
    "own_traffic",
    "niches",
    "niche_channels",
    "niche_scores",
    "competitor_analyses",
    "video_summaries",
    "quota_ledger",
    "runs",
    "channel_metrics",
    "api_cache",
    "collector_state",
    "decisions",
}

EXPECTED_INDEXES = {
    ("channel_snapshots", ("channel_id", "captured_at")),
    ("video_snapshots", ("video_id", "captured_at")),
    ("videos", ("channel_id", "published_at")),
    ("niche_scores", ("niche_id", "scored_at")),
}

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def _tables(c: sqlite3.Connection) -> set[str]:
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def _columns(c: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]


def _seed_channel_and_video(c: sqlite3.Connection) -> None:
    repo.upsert_channel(c, "UC1", role="competitor", title="Animals")
    repo.upsert_video(c, "v1", channel_id="UC1", published_at="2026-09-01T10:00:00Z")


# --- connect and migrations ---------------------------------------------------------------


def test_fresh_db_has_exactly_the_expected_tables(conn: sqlite3.Connection) -> None:
    assert _tables(conn) == EXPECTED_TABLES


def test_expected_indexes_exist(conn: sqlite3.Connection) -> None:
    found = set()
    for idx in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'"):
        cols = tuple(r["name"] for r in conn.execute(f"PRAGMA index_info({idx['name']})"))
        found.add((idx["tbl_name"], cols))
    assert EXPECTED_INDEXES <= found


def test_own_analytics_uses_a_window_not_a_day(conn: sqlite3.Connection) -> None:
    cols = _columns(conn, "own_analytics")
    assert "window_start" in cols and "window_end" in cols
    assert "day" not in cols


def test_addition_tables_have_the_issue_columns(conn: sqlite3.Connection) -> None:
    assert _columns(conn, "channel_metrics")[1:] == [
        "channel_id",
        "computed_at",
        "window",
        "format",
        "metrics_json",
    ]
    assert _columns(conn, "api_cache") == ["key", "etag", "body_json", "fetched_at"]
    assert _columns(conn, "collector_state") == ["kind", "key", "done_at"]
    assert _columns(conn, "decisions") == ["id", "kind", "target_id", "decision", "decided_at"]


def test_pragmas_and_row_factory(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.row_factory is sqlite3.Row


def test_connect_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "x.sqlite"
    connect(path).close()
    assert path.is_file()


def test_connect_twice_applies_nothing_new(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite"
    connect(path).close()
    c = connect(path)
    rows = c.execute("SELECT version, applied_at FROM schema_migrations").fetchall()
    assert [r["version"] for r in rows] == [1, 2, 3, 4]
    assert ISO_UTC.match(rows[0]["applied_at"])
    assert migrate(c) == []
    c.close()


def test_migrations_apply_in_order_and_only_once(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0002_second.sql").write_text("ALTER TABLE t ADD COLUMN b TEXT;", encoding="utf-8")
    (mig / "0001_first.sql").write_text("CREATE TABLE t (a TEXT);", encoding="utf-8")
    c = sqlite3.connect(tmp_path / "m.sqlite")
    assert migrate(c, mig) == [1, 2]
    assert migrate(c, mig) == []
    (mig / "0003_third.sql").write_text("CREATE TABLE u (x);", encoding="utf-8")
    assert migrate(c, mig) == [3]
    c.close()


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0001_bad.sql").write_text("CREATE TABLE ok (a);\nNOT SQL;", encoding="utf-8")
    c = sqlite3.connect(tmp_path / "m.sqlite")
    with pytest.raises(MigrationError):
        migrate(c, mig)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "ok" not in names
    assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0
    c.close()


def test_misnamed_migration_is_refused(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "first.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        db.migration_files(mig)


# --- time ---------------------------------------------------------------------------------


def test_now_utc_format() -> None:
    assert ISO_UTC.match(now_utc())


def test_to_utc_iso_converts_offsets_and_refuses_naive() -> None:
    bst = timezone(timedelta(hours=1))
    assert to_utc_iso(datetime(2026, 6, 1, 12, 0, 0, tzinfo=bst)) == "2026-06-01T11:00:00Z"
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 6, 1, 12, 0, 0))


def test_only_db_reads_the_clock() -> None:
    pkg = Path(db.__file__).parent.parent
    offenders = [
        p.relative_to(pkg).as_posix()
        for p in pkg.rglob("*.py")
        if "datetime.now" in p.read_text(encoding="utf-8") and p != Path(db.__file__)
    ]
    assert offenders == []


# --- repo round-trips ---------------------------------------------------------------------


def test_channel_round_trip(conn: sqlite3.Connection) -> None:
    repo.upsert_channel(
        conn,
        "UC1",
        role="competitor",
        title="Animals",
        custom_url="@animals",
        country="GB",
        created_at=datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC),
        uploads_playlist_id="UU1",
    )
    row = repo.get_channel(conn, "UC1")
    assert row["title"] == "Animals"
    assert row["created_at"] == "2020-01-02T03:04:05Z"
    assert row["uploads_playlist_id"] == "UU1"
    assert row["status"] is None
    assert ISO_UTC.match(row["first_seen"])
    first_seen = row["first_seen"]

    repo.upsert_channel(conn, "UC1", role="niche_sample", title="Animals 2")
    row = repo.get_channel(conn, "UC1")
    assert row["title"] == "Animals 2"
    assert row["custom_url"] == "@animals"  # None keeps the stored value
    assert row["role"] == "competitor"  # role is set once
    assert row["first_seen"] == first_seen


def test_upsert_channel_rejects_unknown_role(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        repo.upsert_channel(conn, "UC1", role="friend")


def test_set_channel_status(conn: sqlite3.Connection) -> None:
    repo.upsert_channel(conn, "UC1", role="competitor")
    repo.set_channel_status(conn, "UC1", "approved")
    assert repo.get_channel(conn, "UC1")["status"] == "approved"
    repo.set_channel_status(conn, "UC1", None)
    assert repo.get_channel(conn, "UC1")["status"] is None
    with pytest.raises(ValueError):
        repo.set_channel_status(conn, "UC1", "maybe")
    with pytest.raises(LookupError):
        repo.set_channel_status(conn, "UCnope", "approved")


def test_channel_snapshot_round_trip(conn: sqlite3.Connection) -> None:
    repo.upsert_channel(conn, "UC1", role="own")
    assert repo.latest_channel_snapshot(conn, "UC1") is None
    repo.add_channel_snapshot(
        conn, "UC1", subs=10, view_count=100, video_count=3, captured_at="2026-09-01T00:00:00Z"
    )
    repo.add_channel_snapshot(
        conn, "UC1", subs=12, view_count=150, video_count=4, captured_at="2026-09-08T00:00:00Z"
    )
    latest = repo.latest_channel_snapshot(conn, "UC1")
    assert (latest["subs"], latest["view_count"], latest["video_count"]) == (12, 150, 4)
    assert latest["captured_at"] == "2026-09-08T00:00:00Z"


def test_video_round_trip(conn: sqlite3.Connection) -> None:
    repo.upsert_channel(conn, "UC1", role="competitor")
    repo.upsert_video(
        conn,
        "v1",
        channel_id="UC1",
        title="Top 5 sharks",
        tags=["sharks", "top 5"],
        published_at="2026-08-01T10:00:00Z",
        duration_s=45,
        is_short=True,
        category_id="15",
    )
    repo.upsert_video(conn, "v2", channel_id="UC1", published_at="2026-09-01T10:00:00Z")
    repo.upsert_video(conn, "v0", channel_id="UC1", published_at="2025-01-01T10:00:00Z")
    row = repo.get_video(conn, "v1")
    assert row["title"] == "Top 5 sharks"
    assert row["tags_json"] == '["sharks", "top 5"]'
    assert row["is_short"] == 1
    assert row["duration_s"] == 45

    repo.upsert_video(conn, "v1", channel_id="UC1", title="Top 5 sharks (new)")
    row = repo.get_video(conn, "v1")
    assert row["title"] == "Top 5 sharks (new)"
    assert row["duration_s"] == 45

    assert [r["id"] for r in repo.videos_for_channel(conn, "UC1")] == ["v2", "v1", "v0"]
    since = datetime(2026, 7, 1, tzinfo=UTC)
    assert [r["id"] for r in repo.videos_for_channel(conn, "UC1", since=since)] == ["v2", "v1"]
    assert [r["id"] for r in repo.videos_for_channel(conn, "UC1", "2026-08-15T00:00:00Z")] == ["v2"]


def test_two_video_snapshots_make_two_rows(conn: sqlite3.Connection) -> None:
    _seed_channel_and_video(conn)
    a = repo.add_video_snapshot(conn, "v1", views=100, likes=5, comments=1)
    b = repo.add_video_snapshot(conn, "v1", views=250, likes=9, comments=2)
    assert a != b
    rows = conn.execute("SELECT * FROM video_snapshots WHERE video_id = 'v1'").fetchall()
    assert len(rows) == 2
    assert all(ISO_UTC.match(r["captured_at"]) for r in rows)
    assert repo.latest_video_snapshot(conn, "v1")["views"] == 250


def test_snapshot_needs_an_existing_video(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_video_snapshot(conn, "nope", views=1, likes=0, comments=0)


def test_helpers_do_not_commit(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite"
    c = connect(path)
    repo.upsert_channel(c, "UC1", role="own")
    other = connect(path)
    assert repo.get_channel(other, "UC1") is None
    c.commit()
    assert repo.get_channel(other, "UC1") is not None
    other.close()
    c.close()


# --- append-only --------------------------------------------------------------------------


def test_repo_has_no_update_or_delete_helper_for_snapshots() -> None:
    for name, fn in inspect.getmembers(repo, inspect.isfunction):
        if fn.__module__ != repo.__name__:
            continue
        if name.startswith(("update_", "delete_")):
            assert "_snapshots" not in inspect.getsource(fn), name


def test_repo_source_never_updates_or_deletes_a_snapshot() -> None:
    source = inspect.getsource(repo)
    pattern = re.compile(r"(UPDATE\s+\w*_snapshots|DELETE\s+FROM\s+\w*_snapshots)", re.I)
    assert pattern.search(source) is None


@pytest.mark.parametrize("table", ["channel_snapshots", "video_snapshots"])
def test_schema_refuses_snapshot_update_and_delete(conn: sqlite3.Connection, table: str) -> None:
    _seed_channel_and_video(conn)
    repo.add_channel_snapshot(conn, "UC1", subs=1, view_count=1, video_count=1)
    repo.add_video_snapshot(conn, "v1", views=1, likes=1, comments=1)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(f"UPDATE {table} SET captured_at = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(f"DELETE FROM {table}")
