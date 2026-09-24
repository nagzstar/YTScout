"""Thin typed helpers over the store. Plain SQL, no ORM.

Helpers do not commit: the caller owns the transaction (``with conn:``), so a collector
can checkpoint a batch at once. Snapshots are only ever added and read here; no helper
updates or deletes a ``*_snapshots`` row, and the schema's triggers refuse it anyway.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from ytscout.store.db import now_utc, to_utc_iso

CHANNEL_ROLES = ("own", "competitor", "niche_sample")
CHANNEL_STATUSES = ("approved", "rejected", "watch")


def _ts(value: str | datetime | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return to_utc_iso(value)


def upsert_channel(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    role: str,
    title: str | None = None,
    custom_url: str | None = None,
    country: str | None = None,
    created_at: str | datetime | None = None,
    uploads_playlist_id: str | None = None,
    last_refreshed: str | datetime | None = None,
) -> None:
    """Insert a channel or refresh its metadata.

    On conflict, ``None`` arguments keep the stored value; ``first_seen``, ``role`` and
    ``status`` are never changed here (status goes through ``set_channel_status``).
    """
    if role not in CHANNEL_ROLES:
        raise ValueError(f"role must be one of {CHANNEL_ROLES}, got {role!r}")
    conn.execute(
        """
        INSERT INTO channels (id, title, custom_url, country, created_at,
                              uploads_playlist_id, role, first_seen, last_refreshed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            title = COALESCE(excluded.title, channels.title),
            custom_url = COALESCE(excluded.custom_url, channels.custom_url),
            country = COALESCE(excluded.country, channels.country),
            created_at = COALESCE(excluded.created_at, channels.created_at),
            uploads_playlist_id = COALESCE(excluded.uploads_playlist_id,
                                           channels.uploads_playlist_id),
            last_refreshed = COALESCE(excluded.last_refreshed, channels.last_refreshed)
        """,
        (
            channel_id,
            title,
            custom_url,
            country,
            _ts(created_at),
            uploads_playlist_id,
            role,
            now_utc(),
            _ts(last_refreshed),
        ),
    )


def set_channel_status(conn: sqlite3.Connection, channel_id: str, status: str | None) -> None:
    """Set a channel's review status (``approved``/``rejected``/``watch`` or ``None``)."""
    if status is not None and status not in CHANNEL_STATUSES:
        raise ValueError(f"status must be one of {CHANNEL_STATUSES} or None, got {status!r}")
    cur = conn.execute("UPDATE channels SET status = ? WHERE id = ?", (status, channel_id))
    if cur.rowcount == 0:
        raise LookupError(f"no channel {channel_id!r}")


def get_channel(conn: sqlite3.Connection, channel_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()


def add_channel_snapshot(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    subs: int | None,
    view_count: int | None,
    video_count: int | None,
    captured_at: str | datetime | None = None,
) -> int:
    """Append one channel snapshot; return its row id. ``captured_at`` defaults to now."""
    cur = conn.execute(
        "INSERT INTO channel_snapshots (channel_id, captured_at, subs, view_count, video_count)"
        " VALUES (?, ?, ?, ?, ?)",
        (channel_id, _ts(captured_at) or now_utc(), subs, view_count, video_count),
    )
    return int(cur.lastrowid or 0)


def latest_channel_snapshot(conn: sqlite3.Connection, channel_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM channel_snapshots WHERE channel_id = ?"
        " ORDER BY captured_at DESC, id DESC LIMIT 1",
        (channel_id,),
    ).fetchone()


def upsert_video(
    conn: sqlite3.Connection,
    video_id: str,
    *,
    channel_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: Sequence[str] | None = None,
    published_at: str | datetime | None = None,
    duration_s: int | None = None,
    is_short: bool | None = None,
    category_id: str | None = None,
) -> None:
    """Insert a video or refresh its metadata; ``None`` arguments keep the stored value."""
    conn.execute(
        """
        INSERT INTO videos (id, channel_id, title, description, tags_json, published_at,
                            duration_s, is_short, category_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            channel_id = excluded.channel_id,
            title = COALESCE(excluded.title, videos.title),
            description = COALESCE(excluded.description, videos.description),
            tags_json = COALESCE(excluded.tags_json, videos.tags_json),
            published_at = COALESCE(excluded.published_at, videos.published_at),
            duration_s = COALESCE(excluded.duration_s, videos.duration_s),
            is_short = COALESCE(excluded.is_short, videos.is_short),
            category_id = COALESCE(excluded.category_id, videos.category_id)
        """,
        (
            video_id,
            channel_id,
            title,
            description,
            json.dumps(list(tags), ensure_ascii=False) if tags is not None else None,
            _ts(published_at),
            duration_s,
            None if is_short is None else int(is_short),
            category_id,
        ),
    )


def get_video(conn: sqlite3.Connection, video_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()


def videos_for_channel(
    conn: sqlite3.Connection, channel_id: str, since: str | datetime | None = None
) -> list[sqlite3.Row]:
    """A channel's videos, newest first; ``since`` keeps those published at or after it."""
    if since is None:
        return conn.execute(
            "SELECT * FROM videos WHERE channel_id = ? ORDER BY published_at DESC, id",
            (channel_id,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM videos WHERE channel_id = ? AND published_at >= ?"
        " ORDER BY published_at DESC, id",
        (channel_id, _ts(since)),
    ).fetchall()


def add_video_snapshot(
    conn: sqlite3.Connection,
    video_id: str,
    *,
    views: int | None,
    likes: int | None,
    comments: int | None,
    captured_at: str | datetime | None = None,
) -> int:
    """Append one video snapshot; return its row id. ``captured_at`` defaults to now."""
    cur = conn.execute(
        "INSERT INTO video_snapshots (video_id, captured_at, views, likes, comments)"
        " VALUES (?, ?, ?, ?, ?)",
        (video_id, _ts(captured_at) or now_utc(), views, likes, comments),
    )
    return int(cur.lastrowid or 0)


def latest_video_snapshot(conn: sqlite3.Connection, video_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM video_snapshots WHERE video_id = ?"
        " ORDER BY captured_at DESC, id DESC LIMIT 1",
        (video_id,),
    ).fetchone()


# --- quota ledger -------------------------------------------------------------------------


def quota_used(conn: sqlite3.Connection, day_pacific: str) -> int:
    """Units charged on ``day_pacific`` (``YYYY-MM-DD``); 0 if the day has no row."""
    row = conn.execute(
        "SELECT units_used FROM quota_ledger WHERE day_pacific = ?", (day_pacific,)
    ).fetchone()
    return int(row[0]) if row else 0


def add_quota_used(conn: sqlite3.Connection, day_pacific: str, units: int) -> None:
    """Add ``units`` to the day's total, creating the row if needed."""
    conn.execute(
        "INSERT INTO quota_ledger (day_pacific, units_used) VALUES (?, ?)"
        " ON CONFLICT(day_pacific) DO UPDATE SET units_used = units_used + excluded.units_used",
        (day_pacific, units),
    )


# --- api cache ----------------------------------------------------------------------------


def get_api_cache(conn: sqlite3.Connection, key: str) -> tuple[str | None, dict] | None:
    """``(etag, body)`` cached under ``key``, or ``None``."""
    row = conn.execute("SELECT etag, body_json FROM api_cache WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return row["etag"], json.loads(row["body_json"])


def put_api_cache(conn: sqlite3.Connection, key: str, etag: str | None, body: dict) -> None:
    """Store or replace the response cached under ``key``."""
    conn.execute(
        "INSERT INTO api_cache (key, etag, body_json, fetched_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET etag = excluded.etag,"
        " body_json = excluded.body_json, fetched_at = excluded.fetched_at",
        (key, etag, json.dumps(body, sort_keys=True), now_utc()),
    )
