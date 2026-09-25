"""``collect --niches``: the cheap weekly refresh of every tracked niche (DESIGN.md §5.4).

Tracked means ``niches.status in ('track', 'tracking')``. Per niche, for the channels in
``niche_channels``:

1. ``channels.list`` in batches of 50 → metadata and one snapshot per channel.
2. One ``playlistItems.list`` page (the newest 50 uploads) per channel; no paging.
3. ``videos.list`` for every video on that page that is new to the DB or published within
   ``RECENT_DAYS``, pooled across the niche's channels 50 ids per call → upsert and
   snapshot.

Never ``search.list``: re-sampling a niche is ``scout validate``'s job. A 30-channel niche
costs ``1 + 30 + ceil(recent / 50)`` units, about 35-40 a week. A channel shared by two
tracked niches is refreshed once per run, under the first (lowest id) niche.

Each batch commits as it lands. A quota stop ends the run and is returned in
``result.stopped``; the niches in ``result.finished`` were refreshed in full.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ytscout.collect.competitors import RECENT_DAYS
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


@dataclass
class NicheReport:
    """What the refresh did for one niche."""

    niche_id: int
    label: str
    channels: int = 0
    shared: int = 0  # already refreshed under an earlier niche this run
    channel_batches: int = 0
    pages: int = 0
    video_batches: int = 0
    videos: int = 0
    units: int = 0
    finished: bool = False


@dataclass
class NicheRefreshResult:
    counts: Counts = field(default_factory=Counts)
    reports: list[NicheReport] = field(default_factory=list)

    @property
    def stopped(self) -> QuotaExhausted | None:
        return self.counts.stopped

    @property
    def finished(self) -> list[int]:
        return [r.niche_id for r in self.reports if r.finished]


def niche_label(niche: Mapping[str, Any]) -> str:
    return niche.get("label") or f"{niche.get('format')} × {niche.get('topic')}"


def _refresh_channels(
    api: DataApi,
    conn: sqlite3.Connection,
    ids: Sequence[str],
    now: datetime,
    report: NicheReport,
    counts: Counts,
) -> dict[str, str | None]:
    """Step 1. Returns ``{id: uploads playlist id}`` for every channel the API returned."""
    found: dict[str, str | None] = {}
    for batch in batches(list(ids)):
        response = api.channels(batch)
        report.channel_batches += 1
        with conn:
            for item in response.get("items", []):
                snippet = item.get("snippet") or {}
                stats = item.get("statistics") or {}
                related = (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
                # role only applies to a new row; an existing competitor keeps its role.
                repo.upsert_channel(
                    conn,
                    item["id"],
                    role="niche_sample",
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
                counts.channels += 1
                counts.channel_snapshots += 1
                found[item["id"]] = related.get("uploads")
    return found


def _first_page(
    api: DataApi,
    conn: sqlite3.Connection,
    uploads: str,
    cutoff: datetime,
) -> list[str]:
    """Step 2: the ids on the newest uploads page that are new or recent, newest first."""
    wanted: list[str] = []
    for video_id, published in upload_page(api.playlist_items(uploads)):
        known = repo.get_video(conn, video_id)
        published = published or (known["published_at"] if known else None)
        when = parse_dt(published) if published else None
        if (known is None or when is None or when >= cutoff) and video_id not in wanted:
            wanted.append(video_id)
    return wanted


def _refresh_niche(
    api: DataApi,
    conn: sqlite3.Connection,
    niche: Mapping[str, Any],
    seen: set[str],
    *,
    shorts_max_seconds: int,
    now: datetime,
    report: NicheReport,
    counts: Counts,
) -> None:
    dry_run = api.ledger.dry_run
    cutoff = now - timedelta(days=RECENT_DAYS)
    rows = {c["id"]: c for c in niche["channels"]}
    ids = [cid for cid in rows if cid not in seen]
    report.channels = len(rows)
    report.shared = len(rows) - len(ids)
    if not ids:
        return
    found = _refresh_channels(api, conn, ids, now, report, counts)
    seen.update(ids)

    owner: dict[str, str] = {}
    for cid in ids:
        uploads = found.get(cid) or rows[cid].get("uploads_playlist_id")
        if not uploads and dry_run:
            uploads = DRY_RUN_UPLOADS
        if not uploads:
            continue
        wanted = _first_page(api, conn, uploads, cutoff)
        report.pages += 1
        if dry_run and not wanted:
            # One stand-in per channel, so the plan pools them as a real run would.
            wanted = [f"{DRY_RUN_VIDEO_IDS[0]} of {cid}"]
        for video_id in wanted:
            owner.setdefault(video_id, cid)

    for batch in batches(list(owner)):
        response = api.videos(batch)
        report.video_batches += 1
        with conn:
            for item in response.get("items", []):
                store_video(conn, item, owner.get(item["id"], ""), shorts_max_seconds)
                report.videos += 1
                counts.videos += 1
                counts.video_snapshots += 1


def collect_niches(
    api: DataApi,
    conn: sqlite3.Connection,
    niches: Sequence[Mapping[str, Any]],
    *,
    shorts_max_seconds: int,
    now: datetime,
) -> NicheRefreshResult:
    """Refresh each of ``niches`` (``id``, ``label``, ``format``, ``topic`` and ``channels``:
    rows with ``id``, ``uploads_playlist_id``), in order. Stops at the first quota refusal.
    """
    result = NicheRefreshResult()
    seen: set[str] = set()
    for niche in niches:
        report = NicheReport(int(niche["id"]), niche_label(niche))
        result.reports.append(report)
        before = api.ledger.run_used
        try:
            _refresh_niche(
                api,
                conn,
                niche,
                seen,
                shorts_max_seconds=shorts_max_seconds,
                now=now,
                report=report,
                counts=result.counts,
            )
            report.finished = True
        except QuotaExhausted as exc:
            result.counts.stopped = exc
            return result
        finally:
            report.units = api.ledger.run_used - before
    return result


def tracked_niches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every tracked niche as a dict with its ``channels`` rows, ready for ``collect_niches``."""
    return [
        dict(niche) | {"channels": [dict(c) for c in repo.niche_channel_rows(conn, niche["id"])]}
        for niche in repo.tracking_niches(conn)
    ]
