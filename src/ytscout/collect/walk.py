"""The uploads walk: playlist → videos → snapshots, for one channel.

Shared by ``collect --own`` (004), competitors (006) and the metrics refresh (010).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ytscout.store import repo
from ytscout.youtube import DataApi, QuotaExhausted, parse_duration
from ytscout.youtube.client import MAX_IDS_PER_CALL

# Stand-in ids a --dry-run plans with: DryRunTransport answers every call with no items,
# so the walk would otherwise stop before planning the calls a real run makes.
DRY_RUN_UPLOADS = "<uploads playlist>"
DRY_RUN_VIDEO_IDS = ("<video ids from the playlist>",)


@dataclass
class Counts:
    """Rows written by a collector run, and the quota stop that ended it early, if any."""

    channels: int = 0
    channel_snapshots: int = 0
    videos: int = 0
    video_snapshots: int = 0
    stopped: QuotaExhausted | None = None

    @property
    def snapshots(self) -> int:
        return self.channel_snapshots + self.video_snapshots


def to_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _video_id(item: Mapping[str, Any]) -> str | None:
    details = item.get("contentDetails") or {}
    resource = (item.get("snippet") or {}).get("resourceId") or {}
    return details.get("videoId") or resource.get("videoId")


def upload_page(item_page: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    """``(video id, published_at)`` for each item of one ``playlistItems.list`` page."""
    entries: list[tuple[str, str | None]] = []
    for item in item_page.get("items", []):
        video_id = _video_id(item)
        if video_id:
            details = item.get("contentDetails") or {}
            published = details.get("videoPublishedAt") or (item.get("snippet") or {}).get(
                "publishedAt"
            )
            entries.append((video_id, published))
    return entries


def batches(ids: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(ids), MAX_IDS_PER_CALL):
        yield ids[start : start + MAX_IDS_PER_CALL]


def store_video(
    conn: sqlite3.Connection, item: Mapping[str, Any], channel_id: str, shorts_max_seconds: int
) -> None:
    """Upsert one ``videos.list`` item and append its stats snapshot. Does not commit."""
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    raw_duration = (item.get("contentDetails") or {}).get("duration")
    duration_s = parse_duration(raw_duration) if raw_duration else None
    # A plain duration cut-off for now. DESIGN.md §5.3 wants the channel's own format mix
    # to decide Shorts vs long-form; that refinement comes with the niche scout.
    is_short = None if duration_s is None else duration_s <= shorts_max_seconds
    repo.upsert_video(
        conn,
        item["id"],
        channel_id=snippet.get("channelId") or channel_id,
        title=snippet.get("title"),
        description=snippet.get("description"),
        # The API omits `tags` when a video has none, so absence means an empty list.
        tags=snippet.get("tags", []),
        published_at=snippet.get("publishedAt"),
        duration_s=duration_s,
        is_short=is_short,
        category_id=snippet.get("categoryId"),
    )
    repo.add_video_snapshot(
        conn,
        item["id"],
        views=to_int(stats.get("viewCount")),
        likes=to_int(stats.get("likeCount")),
        comments=to_int(stats.get("commentCount")),
    )


def walk_uploads(
    api: DataApi,
    conn: sqlite3.Connection,
    channel: Mapping[str, Any],
    *,
    limit: int,
    shorts_max_seconds: int,
    counts: Counts | None = None,
) -> Counts:
    """Collect the newest ``limit`` uploads of ``channel`` (a ``channels`` row or mapping).

    Pages ``playlistItems.list`` until ``limit`` ids or the playlist ends, then
    ``videos.list`` in batches of 50; each batch is committed as it lands. A
    ``QuotaExhausted`` stops the walk and is returned in ``counts.stopped``, with every
    finished batch already committed.
    """
    counts = counts if counts is not None else Counts()
    dry_run = api.ledger.dry_run
    uploads = channel["uploads_playlist_id"] or (DRY_RUN_UPLOADS if dry_run else None)
    if not uploads:
        raise ValueError(f"channel {channel['id']} has no uploads playlist id")
    try:
        ids: list[str] = []
        for item in api.iter_playlist_items(uploads):
            video_id = _video_id(item)
            if video_id and video_id not in ids:
                ids.append(video_id)
            if len(ids) >= limit:
                break
        if dry_run and not ids:
            ids = list(DRY_RUN_VIDEO_IDS)
        for batch in batches(ids):
            response = api.videos(batch)
            with conn:
                for item in response.get("items", []):
                    store_video(conn, item, channel["id"], shorts_max_seconds)
                    counts.videos += 1
                    counts.video_snapshots += 1
    except QuotaExhausted as exc:
        counts.stopped = exc
    return counts
