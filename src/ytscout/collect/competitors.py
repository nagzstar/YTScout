"""``collect --competitors``: the weekly refresh of every tracked channel.

Tracked means ``status in ('approved', 'watch')`` plus the own channel. Per run:

1. ``channels.list`` in batches of 50 → metadata and one snapshot per channel.
2. Per channel, page the uploads playlist (newest first) and stop after the first page
   whose oldest video is already in the DB *and* older than ``RECENT_DAYS``: everything
   beyond it is known and old.
3. ``videos.list`` for every walked video that is new or published within
   ``RECENT_DAYS`` → upsert and snapshot. Older known videos keep their last snapshot;
   they barely move (DESIGN.md §8.1).

A settled channel costs about 3 units a week: its share of a channels call, one playlist
page and one videos batch. A channel's first refresh walks its whole playlist.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ytscout.collect.walk import (
    DRY_RUN_UPLOADS,
    DRY_RUN_VIDEO_IDS,
    Counts,
    batches,
    store_video,
    to_int,
    upload_page,
)
from ytscout.store import repo, to_utc_iso
from ytscout.youtube import DataApi, QuotaExhausted, parse_dt

# Videos published this recently are re-snapshotted every run (DESIGN.md §8.1).
RECENT_DAYS = 90

STOP_KNOWN = f"reached known videos older than {RECENT_DAYS} days"
STOP_END = "end of playlist"
STOP_NO_UPLOADS = "no uploads playlist"
STOP_QUOTA = "quota"


@dataclass
class ChannelReport:
    """What the refresh did for one channel."""

    channel_id: str
    title: str | None
    pages: int = 0
    video_batches: int = 0
    videos: int = 0
    units: int = 0
    stop: str = ""


@dataclass
class CompetitorResult:
    counts: Counts = field(default_factory=Counts)
    channel_batches: int = 0
    channel_units: int = 0
    reports: list[ChannelReport] = field(default_factory=list)

    @property
    def stopped(self) -> QuotaExhausted | None:
        return self.counts.stopped


def _refresh_channels(
    api: DataApi,
    conn: sqlite3.Connection,
    ids: Sequence[str],
    own_channel_id: str,
    now: datetime,
    result: CompetitorResult,
) -> dict[str, dict[str, Any]]:
    """Step 1. Returns ``{id: {"title", "uploads"}}`` for every channel the API returned."""
    found: dict[str, dict[str, Any]] = {}
    for batch in batches(list(ids)):
        before = api.ledger.run_used
        response = api.channels(batch)
        result.channel_batches += 1
        result.channel_units += api.ledger.run_used - before
        with conn:
            for item in response.get("items", []):
                snippet = item.get("snippet") or {}
                stats = item.get("statistics") or {}
                related = (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
                repo.upsert_channel(
                    conn,
                    item["id"],
                    role="own" if item["id"] == own_channel_id else "competitor",
                    title=snippet.get("title"),
                    custom_url=snippet.get("customUrl"),
                    country=snippet.get("country"),
                    created_at=snippet.get("publishedAt"),
                    uploads_playlist_id=related.get("uploads"),
                    last_refreshed=to_utc_iso(now),
                )
                repo.add_channel_snapshot(
                    conn,
                    item["id"],
                    subs=None
                    if stats.get("hiddenSubscriberCount")
                    else to_int(stats.get("subscriberCount")),
                    view_count=to_int(stats.get("viewCount")),
                    video_count=to_int(stats.get("videoCount")),
                    captured_at=now,
                )
                result.counts.channels += 1
                result.counts.channel_snapshots += 1
                found[item["id"]] = {
                    "title": snippet.get("title"),
                    "uploads": related.get("uploads"),
                }
    return found


def _walk(
    api: DataApi,
    conn: sqlite3.Connection,
    uploads: str,
    cutoff: datetime,
    report: ChannelReport,
) -> list[str]:
    """Step 2: page the playlist; return the ids to re-fetch, newest first."""
    wanted: list[str] = []
    token: str | None = None
    while True:
        page = api.playlist_items(uploads, page_token=token)
        report.pages += 1
        entries = upload_page(page)
        oldest: tuple[datetime, bool] | None = None
        for video_id, published in entries:
            known = repo.get_video(conn, video_id)
            published = published or (known["published_at"] if known else None)
            when = parse_dt(published) if published else None
            if (known is None or when is None or when >= cutoff) and video_id not in wanted:
                wanted.append(video_id)
            if when is not None and (oldest is None or when < oldest[0]):
                oldest = (when, known is not None)
        if oldest is not None and oldest[1] and oldest[0] < cutoff:
            report.stop = STOP_KNOWN
            return wanted
        token = page.get("nextPageToken")
        if not token:
            report.stop = STOP_END
            return wanted


def collect_competitors(
    api: DataApi,
    conn: sqlite3.Connection,
    channels: Sequence[Mapping[str, Any]],
    *,
    own_channel_id: str,
    shorts_max_seconds: int,
    now: datetime,
) -> CompetitorResult:
    """Refresh the own channel and every row of ``channels`` (``id``, ``title``,
    ``uploads_playlist_id``). Each batch commits as it lands; a quota stop ends the run
    and is returned in ``result.stopped``.
    """
    result = CompetitorResult()
    known = {c["id"]: c for c in channels}
    ids = [own_channel_id, *(c["id"] for c in channels if c["id"] != own_channel_id)]
    dry_run = api.ledger.dry_run
    cutoff = now - timedelta(days=RECENT_DAYS)
    try:
        found = _refresh_channels(api, conn, ids, own_channel_id, now, result)
    except QuotaExhausted as exc:
        result.counts.stopped = exc
        return result

    for channel_id in ids:
        row = known.get(channel_id) or {}
        info = found.get(channel_id) or {}
        report = ChannelReport(channel_id, info.get("title") or row.get("title"))
        result.reports.append(report)
        uploads = info.get("uploads") or row.get("uploads_playlist_id")
        if not uploads and dry_run:
            uploads = DRY_RUN_UPLOADS
        if not uploads:
            report.stop = STOP_NO_UPLOADS
            continue
        before = api.ledger.run_used
        try:
            ids_to_fetch = _walk(api, conn, uploads, cutoff, report)
            if dry_run and not ids_to_fetch:
                ids_to_fetch = list(DRY_RUN_VIDEO_IDS)
            for batch in batches(ids_to_fetch):
                response = api.videos(batch)
                report.video_batches += 1
                with conn:
                    for item in response.get("items", []):
                        store_video(conn, item, channel_id, shorts_max_seconds)
                        report.videos += 1
                        result.counts.videos += 1
                        result.counts.video_snapshots += 1
        except QuotaExhausted as exc:
            report.stop = STOP_QUOTA
            result.counts.stopped = exc
            return result
        finally:
            report.units = api.ledger.run_used - before
    return result
