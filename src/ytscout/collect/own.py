"""``collect --own``: the own channel's metadata, snapshot and newest uploads."""

from __future__ import annotations

import sqlite3

from ytscout.collect.walk import Counts, to_int, walk_uploads
from ytscout.store import now_utc, repo
from ytscout.youtube import DataApi, QuotaExhausted


class ChannelNotFound(Exception):
    """``channels.list`` returned nothing for the id."""


def collect_own(
    api: DataApi,
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    videos: int,
    shorts_max_seconds: int,
) -> Counts:
    """Refresh the own channel and walk its uploads. Quota stops land in ``stopped``."""
    counts = Counts()
    try:
        response = api.channels([channel_id])
    except QuotaExhausted as exc:
        counts.stopped = exc
        return counts
    items = response.get("items", [])
    if not items:
        if not api.ledger.dry_run:
            raise ChannelNotFound(f"channels.list found no channel {channel_id}")
        return walk_uploads(
            api,
            conn,
            {"id": channel_id, "uploads_playlist_id": None},
            limit=videos,
            shorts_max_seconds=shorts_max_seconds,
            counts=counts,
        )

    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    with conn:
        repo.upsert_channel(
            conn,
            item["id"],
            role="own",
            title=snippet.get("title"),
            custom_url=snippet.get("customUrl"),
            country=snippet.get("country"),
            created_at=snippet.get("publishedAt"),
            uploads_playlist_id=uploads,
            last_refreshed=now_utc(),
        )
        repo.add_channel_snapshot(
            conn,
            item["id"],
            subs=None
            if stats.get("hiddenSubscriberCount")
            else to_int(stats.get("subscriberCount")),
            view_count=to_int(stats.get("viewCount")),
            video_count=to_int(stats.get("videoCount")),
        )
    counts.channels += 1
    counts.channel_snapshots += 1
    channel = repo.get_channel(conn, item["id"])
    assert channel is not None
    return walk_uploads(
        api, conn, channel, limit=videos, shorts_max_seconds=shorts_max_seconds, counts=counts
    )
