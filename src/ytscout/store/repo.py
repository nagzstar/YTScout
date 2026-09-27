"""Thin typed helpers over the store. Plain SQL, no ORM.

Helpers do not commit: the caller owns the transaction (``with conn:``), so a collector
can checkpoint a batch at once. Snapshots are only ever added and read here; no helper
updates or deletes a ``*_snapshots`` row, and the schema's triggers refuse it anyway.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ytscout.store.db import now_utc, to_utc_iso, utc_now

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


def set_channel_discovery(conn: sqlite3.Connection, channel_id: str, discovery: dict) -> None:
    """Replace a channel's ``discovery_json`` (why ``discover`` suggested it)."""
    cur = conn.execute(
        "UPDATE channels SET discovery_json = ? WHERE id = ?",
        (json.dumps(discovery, ensure_ascii=False, sort_keys=True), channel_id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"no channel {channel_id!r}")


def channel_ids_with_status(conn: sqlite3.Connection, status: str) -> set[str]:
    """Ids of every channel whose review status is ``status``."""
    rows = conn.execute("SELECT id FROM channels WHERE status = ?", (status,)).fetchall()
    return {row[0] for row in rows}


def recent_titles(conn: sqlite3.Connection, channel_id: str, limit: int) -> list[str]:
    """The channel's newest ``limit`` video titles, newest first; untitled rows skipped."""
    rows = conn.execute(
        "SELECT title FROM videos WHERE channel_id = ? AND title IS NOT NULL"
        " ORDER BY published_at DESC, id LIMIT ?",
        (channel_id, limit),
    ).fetchall()
    return [row[0] for row in rows]


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


def tracked_channels(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The own channel first, then every ``approved``/``watch`` channel, by title."""
    return conn.execute(
        "SELECT * FROM channels WHERE role = 'own' OR status IN ('approved', 'watch')"
        " ORDER BY role != 'own', title, id"
    ).fetchall()


def video_snapshots_for_channel(conn: sqlite3.Connection, channel_id: str) -> list[sqlite3.Row]:
    """Every snapshot of the channel's videos, oldest first per video."""
    return conn.execute(
        "SELECT s.* FROM video_snapshots s JOIN videos v ON v.id = s.video_id"
        " WHERE v.channel_id = ? ORDER BY s.video_id, s.captured_at, s.id",
        (channel_id,),
    ).fetchall()


# --- channel metrics ----------------------------------------------------------------------


def add_channel_metrics(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    window: str,
    fmt: str,
    metrics: dict,
    computed_at: str | datetime | None = None,
) -> int:
    """Append one ``channel_metrics`` row; readers take the latest per channel/window/format."""
    cur = conn.execute(
        "INSERT INTO channel_metrics (channel_id, computed_at, window, format, metrics_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            channel_id,
            _ts(computed_at) or now_utc(),
            window,
            fmt,
            json.dumps(metrics, ensure_ascii=False, sort_keys=True),
        ),
    )
    return int(cur.lastrowid or 0)


def latest_channel_metrics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The newest ``channel_metrics`` row per channel × window × format."""
    return conn.execute(
        "SELECT m.* FROM channel_metrics m WHERE m.id = ("
        "  SELECT id FROM channel_metrics x WHERE x.channel_id = m.channel_id"
        "  AND x.window = m.window AND x.format = m.format"
        "  ORDER BY x.computed_at DESC, x.id DESC LIMIT 1)"
        " ORDER BY m.channel_id, m.window, m.format"
    ).fetchall()


# --- quota ledger -------------------------------------------------------------------------


def quota_used(conn: sqlite3.Connection, day_pacific: str) -> int:
    """Units charged on ``day_pacific`` (``YYYY-MM-DD``); 0 if the day has no row."""
    row = conn.execute(
        "SELECT units_used FROM quota_ledger WHERE day_pacific = ?", (day_pacific,)
    ).fetchone()
    return int(row[0]) if row else 0


def quota_total(conn: sqlite3.Connection) -> int:
    """Units charged over every day in the ledger (a run may cross Pacific midnight)."""
    row = conn.execute("SELECT COALESCE(SUM(units_used), 0) FROM quota_ledger").fetchone()
    return int(row[0])


def add_quota_used(conn: sqlite3.Connection, day_pacific: str, units: int) -> None:
    """Add ``units`` to the day's total, creating the row if needed."""
    conn.execute(
        "INSERT INTO quota_ledger (day_pacific, units_used) VALUES (?, ?)"
        " ON CONFLICT(day_pacific) DO UPDATE SET units_used = units_used + excluded.units_used",
        (day_pacific, units),
    )


# --- collector checkpoints (032) ---------------------------------------------------------


def mark_collector_done(
    conn: sqlite3.Connection,
    kind: str,
    run_id: str,
    key: str,
    done_at: str | datetime | None = None,
) -> None:
    """Record that ``key`` finished for ``kind`` in logical run ``run_id``."""
    conn.execute(
        "INSERT INTO collector_state (kind, run_id, key, done_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(kind, run_id, key) DO UPDATE SET done_at = excluded.done_at",
        (kind, run_id, key, _ts(done_at) or now_utc()),
    )


def collector_done_keys(
    conn: sqlite3.Connection, kind: str, run_id: str, since: str | datetime
) -> set[str]:
    """Keys of ``kind`` done in ``run_id`` at or after ``since``."""
    rows = conn.execute(
        "SELECT key FROM collector_state WHERE kind = ? AND run_id = ? AND done_at >= ?",
        (kind, run_id, _ts(since)),
    ).fetchall()
    return {row[0] for row in rows}


def clear_collector_state(conn: sqlite3.Connection, before: str | datetime) -> int:
    """Delete checkpoints older than ``before``; return how many went."""
    return conn.execute("DELETE FROM collector_state WHERE done_at < ?", (_ts(before),)).rowcount


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


# --- own analytics (011) -------------------------------------------------------------------

_OWN_ANALYTICS_COLUMNS = (
    "views",
    "est_revenue_usd",
    "rpm_usd",
    "monetized_playbacks",
    "avg_view_duration_s",
    "avg_view_pct",
    "impressions",
    "ctr",
    "sub_delta",
    "minutes_watched",
    "likes",
)


def upsert_own_analytics(
    conn: sqlite3.Connection, video_id: str, window_start: str, window_end: str, **values: object
) -> None:
    """One video's totals over a window. Columns not given are stored as NULL."""
    unknown = set(values) - set(_OWN_ANALYTICS_COLUMNS)
    if unknown:
        raise ValueError(f"unknown own_analytics columns: {sorted(unknown)}")
    columns = ("video_id", "window_start", "window_end", *_OWN_ANALYTICS_COLUMNS, "collected_at")
    row = (
        video_id,
        window_start,
        window_end,
        *(values.get(c) for c in _OWN_ANALYTICS_COLUMNS),
        now_utc(),
    )
    conn.execute(
        f"INSERT OR REPLACE INTO own_analytics ({', '.join(columns)})"
        f" VALUES ({', '.join('?' * len(columns))})",
        row,
    )


def upsert_own_daily(
    conn: sqlite3.Connection,
    day: str,
    views: int | None,
    revenue_usd: float | None,
    monetized_playbacks: int | None,
) -> None:
    """One channel-level day; a re-run replaces it (recent days get revised)."""
    conn.execute(
        "INSERT OR REPLACE INTO own_daily"
        " (day, views, revenue_usd, monetized_playbacks, collected_at) VALUES (?, ?, ?, ?, ?)",
        (day, views, revenue_usd, monetized_playbacks, now_utc()),
    )


def upsert_own_traffic(
    conn: sqlite3.Connection, window_start: str, window_end: str, source: str, views: int | None
) -> None:
    """Channel-level views from one traffic source over a window."""
    conn.execute(
        "INSERT OR REPLACE INTO own_traffic"
        " (window_start, window_end, source, views, collected_at) VALUES (?, ?, ?, ?, ?)",
        (window_start, window_end, source, views, now_utc()),
    )


# --- transcripts --------------------------------------------------------------------------

TRANSCRIPT_STATUSES = ("ok", "unavailable", "blocked", "error")
# A ``blocked`` row keeps its video out of the candidates this long (039).
BLOCKED_RETRY_AFTER = timedelta(days=7)


def transcript_candidates(conn: sqlite3.Connection, limit: int) -> list[str]:
    """Video ids of the own and ``approved``/``watch`` channels with no final transcript row
    (``ok`` or ``unavailable``) and no ``blocked`` row younger than a week, newest first, so
    fresh videos get a run's budget before old failures are retried."""
    blocked_before = to_utc_iso(utc_now() - BLOCKED_RETRY_AFTER)
    rows = conn.execute(
        """
        SELECT v.id FROM videos v JOIN channels c ON c.id = v.channel_id
        WHERE (c.role = 'own' OR c.status IN ('approved', 'watch'))
          AND NOT EXISTS (SELECT 1 FROM transcripts t
                          WHERE t.video_id = v.id
                            AND (t.status IN ('ok', 'unavailable')
                                 OR (t.status = 'blocked' AND t.fetched_at > ?)))
        ORDER BY v.published_at DESC, v.id
        LIMIT ?
        """,
        (blocked_before, limit),
    ).fetchall()
    return [row[0] for row in rows]


def put_transcript(
    conn: sqlite3.Connection,
    video_id: str,
    *,
    language: str,
    text: str | None,
    source: str | None,
    status: str,
) -> None:
    """Record one fetch attempt. A final result (``ok``/``unavailable``) clears the video's
    earlier ``error``/``blocked`` rows; an ``ok`` row is never overwritten by a later attempt."""
    if status not in TRANSCRIPT_STATUSES:
        raise ValueError(f"status must be one of {TRANSCRIPT_STATUSES}, got {status!r}")
    if status in ("ok", "unavailable"):
        conn.execute(
            "DELETE FROM transcripts WHERE video_id = ? AND status IN ('error', 'blocked')",
            (video_id,),
        )
    conn.execute(
        """
        INSERT INTO transcripts (video_id, language, text, source, status, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (video_id, language) DO UPDATE SET
            text = excluded.text, source = excluded.source,
            status = excluded.status, fetched_at = excluded.fetched_at
        WHERE transcripts.status != 'ok'
        """,
        (video_id, language, text, source, status, now_utc()),
    )


def get_transcripts(conn: sqlite3.Connection, video_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? ORDER BY language", (video_id,)
    ).fetchall()


# --- runs ---------------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, kind: str, log_path: str | None = None) -> int:
    """Insert a ``runs`` row started now; return its id."""
    cur = conn.execute(
        "INSERT INTO runs (started_at, kind, log_path) VALUES (?, ?, ?)",
        (now_utc(), kind, log_path),
    )
    return int(cur.lastrowid or 0)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    units_used: int | None = None,
    claude_calls: int | None = None,
    claude_input_tokens: int | None = None,
    claude_output_tokens: int | None = None,
    claude_cost_usd_est: float | None = None,
    error_tail: str | None = None,
) -> None:
    """Stamp a ``runs`` row with ``finished_at`` = now, ``status`` and what it spent."""
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, units_used = ?, claude_calls = ?,"
        " claude_input_tokens = ?, claude_output_tokens = ?, claude_cost_usd_est = ?,"
        " error_tail = ? WHERE id = ?",
        (
            now_utc(),
            status,
            units_used,
            claude_calls,
            claude_input_tokens,
            claude_output_tokens,
            claude_cost_usd_est,
            error_tail,
            run_id,
        ),
    )


# --- decisions ----------------------------------------------------------------------------

# What a person may decide, per target kind (008). Channel decisions are the channel status;
# `undecided` (035) clears it, so a mis-click goes back to Candidates through the audit log.
UNDECIDED = "undecided"
DECISIONS: dict[str, tuple[str, ...]] = {
    "channel": (*CHANNEL_STATUSES, UNDECIDED),
    "niche": ("track", "shelve"),
}


def set_niche_status(conn: sqlite3.Connection, niche_id: int, status: str | None) -> None:
    """Set a niche's review status (``track``/``shelve`` or ``None``)."""
    if status is not None and status not in DECISIONS["niche"]:
        raise ValueError(f"status must be one of {DECISIONS['niche']} or None, got {status!r}")
    cur = conn.execute("UPDATE niches SET status = ? WHERE id = ?", (status, niche_id))
    if cur.rowcount == 0:
        raise LookupError(f"no niche {niche_id!r}")


_READ_ONLY_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
)


def _read_only(action: int, *_args: object) -> int:
    return sqlite3.SQLITE_OK if action in _READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


def competitors_to_decide(
    conn: sqlite3.Connection, where: str | None = None, ids: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Competitor rows for ``ytscout decide`` (035): ``id, title, status, subs``.

    ``where`` is a SQL expression over those four columns (``subs`` is the latest
    snapshot's), typed by Nagz; it runs under an authorizer that allows reads only, so it
    can filter but never write. ``ids`` picks channels by id instead, in the given order.
    Raises ``sqlite3.Error`` for a bad expression.
    """
    inner = (
        "SELECT c.id, c.title, c.status, (SELECT s.subs FROM channel_snapshots s"
        " WHERE s.channel_id = c.id ORDER BY s.captured_at DESC, s.id DESC LIMIT 1) AS subs"
        " FROM channels c WHERE c.role = 'competitor'"
    )
    if where is not None:
        sql = f"SELECT id, title, status, subs FROM ({inner}) WHERE ({where}) ORDER BY title, id"
        conn.set_authorizer(_read_only)
        try:
            return [dict(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.set_authorizer(None)
    found = {
        r["id"]: dict(r)
        for r in conn.execute(
            f"SELECT id, title, status, subs FROM ({inner})"
            f" WHERE id IN ({', '.join('?' for _ in ids)})",
            tuple(ids),
        ).fetchall()
    }
    return [found[i] for i in dict.fromkeys(ids) if i in found]


def record_decision(
    conn: sqlite3.Connection, kind: str, target_id: str, decision: str, decided_at: str
) -> None:
    """Insert a ``decisions`` row and set the target's status to ``decision``.

    ``undecided`` sets a channel's status back to NULL (a candidate again).

    Raises ``ValueError`` for an unknown kind or decision (or a non-integer niche id) and
    ``LookupError`` when the target does not exist; the caller rolls back.
    """
    if kind not in DECISIONS:
        raise ValueError(f"kind must be one of {tuple(DECISIONS)}, got {kind!r}")
    if decision not in DECISIONS[kind]:
        raise ValueError(f"decision for {kind} must be one of {DECISIONS[kind]}, got {decision!r}")
    if kind == "channel":
        set_channel_status(conn, target_id, None if decision == UNDECIDED else decision)
    else:
        try:
            niche_id = int(target_id)
        except ValueError:
            raise ValueError(f"niche id must be an integer, got {target_id!r}") from None
        set_niche_status(conn, niche_id, decision)
    conn.execute(
        "INSERT INTO decisions (kind, target_id, decision, decided_at) VALUES (?, ?, ?, ?)",
        (kind, target_id, decision, decided_at),
    )


# --- video summaries (016) -----------------------------------------------------------------


@dataclass(frozen=True)
class SummaryCandidate:
    """One video ``summary_plan`` picked, with why: ``outlier`` or newest-first filler."""

    video_id: str
    channel_id: str
    channel_title: str | None
    role: str
    views: int | None
    outlier: bool
    is_short: bool = False


def summary_plan(
    conn: sqlite3.Connection,
    prompt_hash: str,
    limit: int,
    *,
    per_channel: int | None = None,
    outlier_multiplier: float = 3.0,
    outlier_window: int = 30,
) -> list[SummaryCandidate]:
    """The videos ``analyse --summaries`` would summarise, in the order it would do them.

    Eligible: videos of the own and ``approved``/``watch`` channels that have had a
    transcript attempt (any status) and no summary under ``prompt_hash``. A summary made
    without a transcript (the packet carried ``unavailable``/``error``) is redone once an
    ``ok`` transcript exists, so titles-only summaries are a stopgap.

    Order (044): each channel's queue is its outliers (latest views >= ``outlier_multiplier``
    x the median of the channel's newest ``outlier_window`` videos of the same format),
    biggest first, then its other videos newest first, cut at ``per_channel``. Own channels'
    queues come first; the competitors' are then interleaved round-robin (by channel id) so
    a daily poster cannot crowd out the rest. The whole list is cut at ``limit``.

    Shorts first (045): the competitor packet compares Shorts only, so a channel's queue
    puts its Shorts ahead of its long-form (and unknown-format) videos, and the plan runs
    every channel's Shorts before any channel's long-form. Long-form still gets summarised
    once the Shorts are done, for a later long-form packet.
    """
    rows = conn.execute(
        """
        SELECT v.id, v.channel_id, c.role, c.title, v.is_short,
               (SELECT views FROM video_snapshots x WHERE x.video_id = v.id
                 ORDER BY x.captured_at DESC, x.id DESC LIMIT 1) AS views,
               (EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.id)
                AND NOT EXISTS (
                    SELECT 1 FROM video_summaries s
                    WHERE s.video_id = v.id AND s.prompt_hash = ?
                      AND (s.transcript_status = 'ok'
                           OR NOT EXISTS (SELECT 1 FROM transcripts t2
                                          WHERE t2.video_id = v.id AND t2.status = 'ok'))))
                   AS eligible
        FROM videos v JOIN channels c ON c.id = v.channel_id
        WHERE c.role = 'own' OR c.status IN ('approved', 'watch')
        ORDER BY v.published_at DESC, v.id
        """,
        (prompt_hash,),
    ).fetchall()

    # Median views of each channel x format's newest `outlier_window` videos (all of them,
    # summarised or not), as in competitor_metrics. Unknown format is its own group.
    window: dict[tuple[str, Any], list[int]] = {}
    for row in rows:
        group = window.setdefault((row["channel_id"], row["is_short"]), [])
        if row["views"] is not None and len(group) < outlier_window:
            group.append(row["views"])
    medians = {key: statistics.median(vals) for key, vals in window.items() if vals}

    outliers: dict[tuple[str, bool], list[SummaryCandidate]] = {}
    newest: dict[tuple[str, bool], list[SummaryCandidate]] = {}
    roles: dict[str, str] = {}
    for row in rows:
        if not row["eligible"]:
            continue
        median = medians.get((row["channel_id"], row["is_short"]))
        views = row["views"]
        is_outlier = (
            views is not None
            and median is not None
            and views > 0
            and views >= outlier_multiplier * median
        )
        is_short = bool(row["is_short"])
        cand = SummaryCandidate(
            row["id"], row["channel_id"], row["title"], row["role"], views, is_outlier, is_short
        )
        roles[row["channel_id"]] = row["role"]
        key = (row["channel_id"], is_short)
        (outliers if is_outlier else newest).setdefault(key, []).append(cand)

    queues: dict[str, list[SummaryCandidate]] = {}
    for channel_id in sorted(roles):
        queue: list[SummaryCandidate] = []
        for is_short in (True, False):
            key = (channel_id, is_short)
            # sorted() is stable, so equal views keep newest-first order.
            queue += sorted(outliers.get(key, []), key=lambda c: -(c.views or 0))
            queue += newest.get(key, [])
        queues[channel_id] = queue if per_channel is None else queue[:per_channel]

    plan: list[SummaryCandidate] = []
    for is_short in (True, False):
        parts = {cid: [c for c in q if c.is_short is is_short] for cid, q in queues.items()}
        for channel_id, queue in parts.items():
            if roles[channel_id] == "own":
                plan.extend(queue)
        others = [q for cid, q in parts.items() if roles[cid] != "own"]
        for i in range(max((len(q) for q in others), default=0)):
            plan.extend(q[i] for q in others if i < len(q))
    return plan[:limit]


def summary_candidates(
    conn: sqlite3.Connection,
    prompt_hash: str,
    limit: int,
    *,
    per_channel: int | None = None,
    outlier_multiplier: float = 3.0,
    outlier_window: int = 30,
) -> list[str]:
    """The video ids of ``summary_plan``, in order."""
    return [
        c.video_id
        for c in summary_plan(
            conn,
            prompt_hash,
            limit,
            per_channel=per_channel,
            outlier_multiplier=outlier_multiplier,
            outlier_window=outlier_window,
        )
    ]


def put_video_summary(
    conn: sqlite3.Connection,
    video_id: str,
    *,
    prompt_hash: str,
    schema_hash: str,
    transcript_status: str,
    summary: dict,
) -> None:
    """Store (or replace, for the same prompt hash) one video's summary."""
    conn.execute(
        """
        INSERT INTO video_summaries
            (video_id, prompt_hash, summary_json, created_at, schema_hash, transcript_status)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (video_id, prompt_hash) DO UPDATE SET
            summary_json = excluded.summary_json, created_at = excluded.created_at,
            schema_hash = excluded.schema_hash, transcript_status = excluded.transcript_status
        """,
        (
            video_id,
            prompt_hash,
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            now_utc(),
            schema_hash,
            transcript_status,
        ),
    )


def get_video_summary(
    conn: sqlite3.Connection, video_id: str, prompt_hash: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM video_summaries WHERE video_id = ? AND prompt_hash = ?",
        (video_id, prompt_hash),
    ).fetchone()


# --- competitor analyses (017) --------------------------------------------------------------

_FORMAT_IS_SHORT = {"shorts": 1, "long": 0}


def summarised_videos_for_channel(
    conn: sqlite3.Connection, channel_id: str, limit: int, fmt: str
) -> list[sqlite3.Row]:
    """The channel's most-viewed videos of format ``fmt`` (``shorts`` or ``long``) that have
    a summary (views desc, then newest), with the latest summary and views, so the
    comparison sees the channel's best work (044) in one format only (045). Videos of
    unknown format (``is_short`` NULL) are in neither.

    One row per video: the summary row with the newest ``created_at`` (whatever its
    prompt hash) and the latest snapshot's views.
    """
    return conn.execute(
        """
        SELECT v.id, v.title, v.published_at, v.duration_s, v.is_short,
               s.summary_json, s.prompt_hash, s.transcript_status,
               (SELECT views FROM video_snapshots x WHERE x.video_id = v.id
                 ORDER BY x.captured_at DESC, x.id DESC LIMIT 1) AS views
        FROM videos v JOIN video_summaries s ON s.rowid = (
            SELECT rowid FROM video_summaries y WHERE y.video_id = v.id
             ORDER BY y.created_at DESC, y.rowid DESC LIMIT 1)
        WHERE v.channel_id = ? AND v.is_short = ?
        ORDER BY views IS NULL, views DESC, v.published_at DESC, v.id
        LIMIT ?
        """,
        (channel_id, _FORMAT_IS_SHORT[fmt], limit),
    ).fetchall()


def video_titles(conn: sqlite3.Connection, video_ids: Iterable[str]) -> dict[str, str | None]:
    """``{video_id: title}`` for the ids that exist in ``videos``."""
    ids = sorted(set(video_ids))
    out: dict[str, str | None] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT id, title FROM videos WHERE id IN ({marks})", chunk):
            out[row["id"]] = row["title"]
    return out


def put_competitor_analysis(
    conn: sqlite3.Connection,
    *,
    prompt_hash: str,
    schema_hash: str,
    packet_path: str | None,
    result: dict | None,
    status: str,
    run_at: str | datetime | None = None,
) -> int:
    """Append one ``competitor_analyses`` row (``schema_version`` holds the schema hash)."""
    cur = conn.execute(
        "INSERT INTO competitor_analyses"
        " (run_at, prompt_hash, schema_version, packet_path, result_json, status)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            _ts(run_at) or now_utc(),
            prompt_hash,
            schema_hash,
            packet_path,
            None if result is None else json.dumps(result, ensure_ascii=False, sort_keys=True),
            status,
        ),
    )
    return int(cur.lastrowid or 0)


def latest_competitor_analysis(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The newest ``competitor_analyses`` row of any status."""
    return conn.execute(
        "SELECT * FROM competitor_analyses ORDER BY run_at DESC, id DESC LIMIT 1"
    ).fetchone()


# --- niches (021) ---------------------------------------------------------------------------

NICHE_FORMATS = ("shorts", "longform")
NICHE_SOURCES = ("llm", "snowball", "seed")
NICHE_STATUS_PROPOSED = "proposed"


def niche_exists(conn: sqlite3.Connection, fmt: str, topic: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM niches WHERE format = ? AND topic = ? LIMIT 1", (fmt, topic)
    ).fetchone()
    return row is not None


def insert_niche(
    conn: sqlite3.Connection,
    *,
    fmt: str,
    topic: str,
    topic_category: str,
    label: str,
    source: str,
    queries: Sequence[str],
    required_steps: Sequence[str],
    meta: dict | None = None,
    status: str = NICHE_STATUS_PROPOSED,
    created_at: str | datetime | None = None,
    seed_channel_ids: Sequence[str] | None = None,
) -> int:
    """Insert one ``niches`` row and return its id.

    Raises ``ValueError`` for an unknown format or source and ``sqlite3.IntegrityError``
    when ``(format, topic)`` already exists; callers de-duplicate with ``niche_exists``.
    """
    if fmt not in NICHE_FORMATS:
        raise ValueError(f"format must be one of {NICHE_FORMATS}, got {fmt!r}")
    if source not in NICHE_SOURCES:
        raise ValueError(f"source must be one of {NICHE_SOURCES}, got {source!r}")
    cur = conn.execute(
        "INSERT INTO niches (format, topic, topic_category, label, status, source, created_at,"
        " queries_json, required_steps_json, meta_json, seed_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fmt,
            topic,
            topic_category,
            label,
            status,
            source,
            _ts(created_at) or now_utc(),
            json.dumps(list(queries), ensure_ascii=False),
            json.dumps(list(required_steps), ensure_ascii=False),
            None if meta is None else json.dumps(meta, ensure_ascii=False, sort_keys=True),
            None if seed_channel_ids is None else json.dumps(list(seed_channel_ids)),
        ),
    )
    return int(cur.lastrowid or 0)


def list_niches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every niche, oldest first."""
    return conn.execute("SELECT * FROM niches ORDER BY id").fetchall()


def get_niche(conn: sqlite3.Connection, niche_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM niches WHERE id = ?", (niche_id,)).fetchone()


NICHE_STATUS_VALIDATED = "validated"
# The dashboard stores the decision as ``track``; DESIGN.md §5.4 calls the state tracking.
NICHE_STATUSES_TRACKING = ("track", "tracking")


def tracking_niches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every niche a person decided to track (``track``/``tracking``), oldest first."""
    marks = ", ".join("?" * len(NICHE_STATUSES_TRACKING))
    return conn.execute(
        f"SELECT * FROM niches WHERE status IN ({marks}) ORDER BY id", NICHE_STATUSES_TRACKING
    ).fetchall()


def niche_channel_rows(conn: sqlite3.Connection, niche_id: int) -> list[sqlite3.Row]:
    """The niche's channels joined to ``channels`` (``id``, ``title``, ``uploads_playlist_id``)."""
    return conn.execute(
        "SELECT c.id, c.title, c.uploads_playlist_id FROM niche_channels nc"
        " JOIN channels c ON c.id = nc.channel_id WHERE nc.niche_id = ? ORDER BY c.id",
        (niche_id,),
    ).fetchall()


def proposed_niches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every ``proposed`` niche in ``created_at`` order (id breaks ties)."""
    return conn.execute(
        "SELECT * FROM niches WHERE status = ? ORDER BY created_at, id", (NICHE_STATUS_PROPOSED,)
    ).fetchall()


def put_niche_channel(
    conn: sqlite3.Connection,
    niche_id: int,
    channel_id: str,
    *,
    is_small: bool | None,
    added_at: str | datetime | None = None,
) -> None:
    """Link a sampled channel to a niche. A re-validation refreshes ``is_small`` only."""
    conn.execute(
        "INSERT INTO niche_channels (niche_id, channel_id, is_small, added_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT (niche_id, channel_id) DO UPDATE SET is_small = excluded.is_small",
        (
            niche_id,
            channel_id,
            None if is_small is None else int(is_small),
            _ts(added_at) or now_utc(),
        ),
    )


def niche_channels(conn: sqlite3.Connection, niche_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM niche_channels WHERE niche_id = ? ORDER BY channel_id", (niche_id,)
    ).fetchall()


def sampled_niche_ids(conn: sqlite3.Connection) -> list[int]:
    """Every niche with at least one linked channel, whatever its status."""
    rows = conn.execute("SELECT DISTINCT niche_id FROM niche_channels ORDER BY niche_id")
    return [row[0] for row in rows]


def niche_channel_recent_videos(
    conn: sqlite3.Connection, niche_id: int, per_channel: int
) -> dict[str, list[sqlite3.Row]]:
    """``{channel_id: newest per_channel videos (title, category_id)}`` for every channel
    linked to the niche; a channel with no stored videos maps to ``[]``."""
    out: dict[str, list[sqlite3.Row]] = {
        row["channel_id"]: [] for row in niche_channels(conn, niche_id)
    }
    rows = conn.execute(
        """
        SELECT channel_id, title, category_id FROM (
            SELECT v.channel_id, v.title, v.category_id,
                   ROW_NUMBER() OVER (PARTITION BY v.channel_id
                                      ORDER BY v.published_at DESC, v.id) AS n
            FROM videos v JOIN niche_channels nc ON nc.channel_id = v.channel_id
            WHERE nc.niche_id = ?
        ) WHERE n <= ? ORDER BY channel_id, n
        """,
        (niche_id, per_channel),
    )
    for row in rows:
        out[row["channel_id"]].append(row)
    return out


def set_niche_channel_exclusion(
    conn: sqlite3.Connection, niche_id: int, channel_id: str, reason: str | None
) -> None:
    """Record why the relevance gate left a channel out of the niche (``None``: counted)."""
    conn.execute(
        "UPDATE niche_channels SET excluded_reason = ? WHERE niche_id = ? AND channel_id = ?",
        (reason, niche_id, channel_id),
    )


def excluded_niche_channels(conn: sqlite3.Connection, niche_id: int) -> list[sqlite3.Row]:
    """The niche's excluded channels: ``channel_id``, ``title``, ``excluded_reason``."""
    return conn.execute(
        "SELECT nc.channel_id, c.title, nc.excluded_reason FROM niche_channels nc"
        " LEFT JOIN channels c ON c.id = nc.channel_id"
        " WHERE nc.niche_id = ? AND nc.excluded_reason IS NOT NULL"
        " ORDER BY c.title COLLATE NOCASE, nc.channel_id",
        (niche_id,),
    ).fetchall()


def mark_niche_validated(
    conn: sqlite3.Connection, niche_id: int, validated_at: str | datetime | None = None
) -> None:
    """``status = 'validated'`` and ``validated_at``: the end of ``scout validate``."""
    cur = conn.execute(
        "UPDATE niches SET status = ?, validated_at = ? WHERE id = ?",
        (NICHE_STATUS_VALIDATED, _ts(validated_at) or now_utc(), niche_id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"no niche {niche_id!r}")


# --- step tags and niche scores (024) -------------------------------------------------------

NICHE_STATUS_SCORED = "scored"
# Tagged and scored: validated onwards, except shelved. `track` is the stored decision value
# (the dashboard's button); `tracking` is DESIGN.md §5.4's name for it, accepted too.
NICHE_STATUSES_TAGGABLE = (NICHE_STATUS_VALIDATED, NICHE_STATUS_SCORED, "track", "tracking")


def niches_to_tag(conn: sqlite3.Connection, prompt_hash: str, limit: int) -> list[sqlite3.Row]:
    """Validated-or-later niches whose tags are missing or came from another prompt."""
    marks = ", ".join("?" * len(NICHE_STATUSES_TAGGABLE))
    return conn.execute(
        f"SELECT * FROM niches WHERE status IN ({marks})"
        " AND (tag_prompt_hash IS NULL OR tag_prompt_hash != ?) ORDER BY id LIMIT ?",
        (*NICHE_STATUSES_TAGGABLE, prompt_hash, limit),
    ).fetchall()


def set_niche_tags(
    conn: sqlite3.Connection,
    niche_id: int,
    *,
    required_steps: Sequence[str],
    needs_specific_footage: bool,
    notes: str,
    prompt_hash: str,
    schema_hash: str,
    tagged_at: str | datetime | None = None,
) -> None:
    """Store the tagger's verdict; it replaces the brainstorm's suspected steps."""
    cur = conn.execute(
        "UPDATE niches SET required_steps_json = ?, needs_specific_footage = ?, tag_notes = ?,"
        " tag_prompt_hash = ?, tag_schema_hash = ?, tagged_at = ? WHERE id = ?",
        (
            json.dumps(list(required_steps), ensure_ascii=False),
            int(needs_specific_footage),
            notes,
            prompt_hash,
            schema_hash,
            _ts(tagged_at) or now_utc(),
            niche_id,
        ),
    )
    if cur.rowcount == 0:
        raise LookupError(f"no niche {niche_id!r}")


def scorable_niches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Validated-or-later niches, tagged or not (the caller counts the untagged)."""
    marks = ", ".join("?" * len(NICHE_STATUSES_TAGGABLE))
    return conn.execute(
        f"SELECT * FROM niches WHERE status IN ({marks}) ORDER BY id", NICHE_STATUSES_TAGGABLE
    ).fetchall()


def niche_sample_videos(conn: sqlite3.Connection, niche_id: int) -> list[sqlite3.Row]:
    """Every video of the niche's channels with its latest snapshot's views (NULL if none)."""
    return conn.execute(
        """
        SELECT v.id, v.channel_id, v.title, v.published_at, v.duration_s,
               (SELECT s.views FROM video_snapshots s WHERE s.video_id = v.id
                ORDER BY s.captured_at DESC, s.id DESC LIMIT 1) AS views
        FROM videos v JOIN niche_channels nc ON nc.channel_id = v.channel_id
        WHERE nc.niche_id = ?
        ORDER BY v.channel_id, v.published_at DESC, v.id
        """,
        (niche_id,),
    ).fetchall()


_NICHE_SCORE_COLUMNS = (
    "opportunity",
    "small_outlier_rate",
    "newcomer_view_share",
    "concentration",
    "newcomer_monthly_views_p25",
    "newcomer_monthly_views_p50",
    "newcomer_monthly_views_p75",
    "rpm_gbp",
    "est_monthly_gbp",
    "manual_hours_per_month",
    "score",
    # 054: months to Partner Programme monetisation (NULL) and whether it is reachable (0/1).
    "months_to_ypp",
    "ypp_reachable",
)


def add_niche_score(
    conn: sqlite3.Connection,
    niche_id: int,
    *,
    scored_at: str | datetime,
    flags: Sequence[str],
    ypp_flags: Sequence[str] = (),
    **values: float | None,
) -> int:
    """Append one ``niche_scores`` row (history: never updated). Returns its id."""
    unknown = set(values) - set(_NICHE_SCORE_COLUMNS)
    if unknown:
        raise ValueError(f"unknown niche_scores columns: {sorted(unknown)}")
    columns = (
        "niche_id",
        "scored_at",
        *_NICHE_SCORE_COLUMNS,
        "confidence_flags_json",
        "ypp_flags_json",
    )
    row = (
        niche_id,
        _ts(scored_at),
        *(values.get(c) for c in _NICHE_SCORE_COLUMNS),
        json.dumps(list(flags)),
        json.dumps(list(ypp_flags)),
    )
    cur = conn.execute(
        f"INSERT INTO niche_scores ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        row,
    )
    return int(cur.lastrowid or 0)


def mark_niche_scored(conn: sqlite3.Connection, niche_id: int) -> None:
    """``validated`` → ``scored``; later statuses (a track decision) are left alone."""
    conn.execute(
        "UPDATE niches SET status = ? WHERE id = ? AND status = ?",
        (NICHE_STATUS_SCORED, niche_id, NICHE_STATUS_VALIDATED),
    )


def own_rpm_rows(conn: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    """Each own video's newest ``own_analytics`` window ending on or after ``since``
    (``YYYY-MM-DD``), with the video's duration (NULL when the video row is missing)."""
    return conn.execute(
        """
        SELECT a.video_id, a.views, a.rpm_usd, v.duration_s
        FROM own_analytics a LEFT JOIN videos v ON v.id = a.video_id
        WHERE a.window_end >= ?
          AND a.window_end = (SELECT MAX(b.window_end) FROM own_analytics b
                              WHERE b.video_id = a.video_id)
        ORDER BY a.video_id, a.window_start
        """,
        (since,),
    ).fetchall()


# --- snowball (026) -------------------------------------------------------------------------


def snowball_sources(conn: sqlite3.Connection) -> list[tuple[str, int | None]]:
    """``(channel_id, niche_id)``: every approved competitor (``niche_id`` None), then every
    channel of a tracking niche, oldest niche first. A channel appears once, first wins."""
    rows = conn.execute(
        "SELECT id AS channel_id, NULL AS niche_id FROM channels"
        " WHERE role = 'competitor' AND status = 'approved'"
        " ORDER BY title, id"
    ).fetchall()
    marks = ", ".join("?" * len(NICHE_STATUSES_TRACKING))
    rows += conn.execute(
        "SELECT nc.channel_id, nc.niche_id FROM niche_channels nc"
        " JOIN niches n ON n.id = nc.niche_id"
        f" WHERE n.status IN ({marks}) ORDER BY n.id, nc.channel_id",
        NICHE_STATUSES_TRACKING,
    ).fetchall()
    seen: dict[str, int | None] = {}
    for row in rows:
        seen.setdefault(row["channel_id"], row["niche_id"])
    return list(seen.items())


def top_videos_by_views(
    conn: sqlite3.Connection, channel_id: str, limit: int
) -> list[tuple[str, str, int]]:
    """``(video_id, title, views)`` of the channel's ``limit`` most-viewed videos, by each
    video's latest snapshot. Videos without a title or a snapshot are skipped."""
    rows = conn.execute(
        "SELECT v.id, v.title, s.views FROM videos v"
        " JOIN video_snapshots s ON s.video_id = v.id"
        " WHERE v.channel_id = ? AND v.title IS NOT NULL AND s.views IS NOT NULL"
        " AND s.captured_at = (SELECT MAX(captured_at) FROM video_snapshots"
        "                      WHERE video_id = v.id)"
        " GROUP BY v.id ORDER BY s.views DESC, v.id LIMIT ?",
        (channel_id, limit),
    ).fetchall()
    return [(r["id"], r["title"], int(r["views"])) for r in rows]


def niche_channel_ids(conn: sqlite3.Connection) -> set[str]:
    """Every channel linked to any niche."""
    return {r[0] for r in conn.execute("SELECT DISTINCT channel_id FROM niche_channels")}


def used_niche_queries(conn: sqlite3.Connection) -> list[str]:
    """Every search query stored on any niche, as written."""
    out: list[str] = []
    for (raw,) in conn.execute("SELECT queries_json FROM niches WHERE queries_json IS NOT NULL"):
        out += [q for q in json.loads(raw) if isinstance(q, str)]
    return out
