"""``score``: read the DB, run the pure scorers in ``ytscout.scoring``, append the results.

``score --competitors`` (010) writes one ``channel_metrics`` row per tracked channel ×
window × format. Niche scores join this command in 024.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ytscout.scoring.metrics import (
    FORMATS,
    MetricsConfig,
    Snapshot,
    VideoStats,
    add_view_shares,
    channel_metrics,
)
from ytscout.store import repo, to_utc_iso
from ytscout.youtube import parse_dt

# Window label (the channel_metrics.window value) -> days.
WINDOWS: dict[str, int] = {"90d": 90, "365d": 365}


def load_videos(conn: sqlite3.Connection, channel_id: str) -> list[VideoStats]:
    """The channel's videos with a publish date, each with all its snapshots."""
    snaps: dict[str, list[Snapshot]] = defaultdict(list)
    for row in repo.video_snapshots_for_channel(conn, channel_id):
        snaps[row["video_id"]].append(Snapshot(parse_dt(row["captured_at"]), row["views"]))
    return [
        VideoStats(
            id=row["id"],
            title=row["title"],
            published_at=parse_dt(row["published_at"]),
            duration_s=row["duration_s"],
            is_short=None if row["is_short"] is None else bool(row["is_short"]),
            snapshots=tuple(snaps.get(row["id"], ())),
        )
        for row in repo.videos_for_channel(conn, channel_id)
        if row["published_at"]
    ]


@dataclass
class ScoreResult:
    channels: int
    rows: int
    computed_at: str


def score_competitors(
    conn: sqlite3.Connection, *, config: MetricsConfig, now: datetime
) -> ScoreResult:
    """Compute every window × format for every tracked channel and append the rows."""
    channels = repo.tracked_channels(conn)
    videos = {c["id"]: load_videos(conn, c["id"]) for c in channels}
    subs: dict[str, int | None] = {}
    for c in channels:
        snap = repo.latest_channel_snapshot(conn, c["id"])
        subs[c["id"]] = snap["subs"] if snap else None

    computed_at = to_utc_iso(now)
    rows = 0
    with conn:
        for label, days in WINDOWS.items():
            for fmt in FORMATS:
                by_channel: dict[str, dict[str, Any]] = {
                    c["id"]: channel_metrics(
                        videos[c["id"]],
                        window_days=days,
                        fmt=fmt,
                        now=now,
                        subs=subs[c["id"]],
                        config=config,
                    )
                    for c in channels
                }
                add_view_shares(by_channel)
                for channel_id, metrics in by_channel.items():
                    repo.add_channel_metrics(
                        conn,
                        channel_id,
                        window=label,
                        fmt=fmt,
                        metrics=metrics,
                        computed_at=computed_at,
                    )
                    rows += 1
    return ScoreResult(channels=len(channels), rows=rows, computed_at=computed_at)
