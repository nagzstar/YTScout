"""``scout validate``: sample a proposed niche's channels and videos (DESIGN.md §5.3).

Per niche (a niche row already has one format):

1. ``search.list`` on its first 3 queries × orders ``viewCount`` and ``date``, one year
   back, 50 results, ``videoDuration`` ``short`` for Shorts and ``medium`` for long-form.
   At most 6 searches (600 units); after 4, stop if they already found ``max_channels``
   distinct channels.
2. The distinct channels, most search hits first, capped at ``max_channels`` (80), go
   through ``channels.list`` → metadata, a snapshot, and ``niche_channels.is_small``.
3. Per channel: one ``playlistItems.list`` page of its uploads, then one ``videos.list``
   for the newest ``videos_per_channel`` (30) → upsert videos and snapshots.
4. ``niches.status = 'validated'`` and ``validated_at``.

The worst case is ``6 × 100 + ceil(80 / 50) + 80 × 2 = 762`` units. A niche is only started
when that much is left under both caps, so a planned stop never wastes the 600 search units
of a niche that cannot finish. Google can still refuse mid-niche (its quota is shared with
the production pipeline); the niche then stays ``proposed`` and its partial rows are
harmless, because snapshots are append-only and a re-run upserts the rest.

Channels already tracked as competitors are sampled too, but ``upsert_channel`` never
changes an existing ``role`` or ``status``.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ytscout.collect.walk import batches, store_video, to_int, upload_page
from ytscout.scoring import ValidationConfig
from ytscout.store import repo, to_utc_iso
from ytscout.youtube import DataApi, QuotaExhausted, parse_dt
from ytscout.youtube.client import MAX_IDS_PER_CALL
from ytscout.youtube.quota import UNIT_COSTS

SEARCH_ORDERS = ("viewCount", "date")
QUERIES_PER_NICHE = 3
MAX_SEARCHES = QUERIES_PER_NICHE * len(SEARCH_ORDERS)
SEARCH_RESULTS = 50
# search.list videoDuration per niche format. The API's `short` is < 4 min, close enough
# to the 180 s Shorts rule; the videos.list duration decides is_short later.
VIDEO_DURATION = {"shorts": "short", "longform": "medium"}
VALIDATABLE = (repo.NICHE_STATUS_PROPOSED, repo.NICHE_STATUS_VALIDATED)


@dataclass(frozen=True)
class SearchSpec:
    query: str
    order: str
    video_duration: str


def niche_queries(niche: Mapping[str, Any]) -> list[str]:
    """The niche's search queries (at most ``QUERIES_PER_NICHE``), blanks dropped."""
    raw = json.loads(niche["queries_json"] or "[]")
    return [q.strip() for q in raw if isinstance(q, str) and q.strip()][:QUERIES_PER_NICHE]


def planned_searches(niche: Mapping[str, Any]) -> list[SearchSpec]:
    """Query-major: q1 viewCount, q1 date, q2 viewCount, ... (≤ ``MAX_SEARCHES``)."""
    fmt = niche["format"]
    if fmt not in VIDEO_DURATION:
        raise ValueError(f"niche {niche['id']} has unknown format {fmt!r}")
    return [
        SearchSpec(q, order, VIDEO_DURATION[fmt])
        for q in niche_queries(niche)
        for order in SEARCH_ORDERS
    ]


def worst_case_units(cfg: ValidationConfig, searches: int = MAX_SEARCHES) -> int:
    """The most one niche can cost: searches, channel batches, one page + one batch each."""
    return (
        searches * UNIT_COSTS[("search", "list")]
        + math.ceil(cfg.max_channels / MAX_IDS_PER_CALL) * UNIT_COSTS[("channels", "list")]
        + cfg.max_channels
        * (UNIT_COSTS[("playlistItems", "list")] + UNIT_COSTS[("videos", "list")])
    )


def is_small(
    subs: int | None, created_at: datetime | None, now: datetime, cfg: ValidationConfig
) -> bool | None:
    """Small = fewer than ``small_subs_max`` subs AND created within ``small_age_days``.

    ``None`` when either fact is unknown (hidden subscriber count, no creation date).
    """
    if subs is None or created_at is None:
        return None
    return subs < cfg.small_subs_max and created_at > now - timedelta(days=cfg.small_age_days)


def rank_channels(hits: Mapping[str, int], cap: int) -> list[str]:
    """The ``cap`` most-hit channels; ties keep first-seen order (``hits`` is ordered)."""
    return sorted(hits, key=lambda channel_id: -hits[channel_id])[:cap]


@dataclass
class NicheReport:
    niche_id: int
    label: str
    fmt: str
    searches: int = 0
    early_stop: bool = False
    distinct_channels: int = 0
    sampled: int = 0
    small: int = 0
    videos: int = 0
    units: int = 0
    validated: bool = False
    skipped: str = ""


def new_report(niche: Mapping[str, Any]) -> NicheReport:
    return NicheReport(int(niche["id"]), niche["label"] or niche["topic"], niche["format"])


@dataclass
class ValidateResult:
    reports: list[NicheReport] = field(default_factory=list)
    stopped: QuotaExhausted | None = None
    # Set when the stop came from the headroom check, before the niche spent anything.
    not_started: NicheReport | None = None

    @property
    def validated(self) -> list[NicheReport]:
        return [r for r in self.reports if r.validated]


def _search(
    api: DataApi,
    specs: Sequence[SearchSpec],
    published_after: datetime,
    cfg: ValidationConfig,
    report: NicheReport,
) -> dict[str, int]:
    hits: dict[str, int] = {}
    for index, spec in enumerate(specs):
        if index == cfg.early_stop_after_searches and len(hits) >= cfg.max_channels:
            report.early_stop = True
            break
        response = api.search(
            spec.query,
            order=spec.order,
            published_after=published_after,
            video_duration=spec.video_duration,
            relevance_language=cfg.language,
            max_results=SEARCH_RESULTS,
        )
        report.searches += 1
        for item in response.get("items", []):
            channel_id = (item.get("snippet") or {}).get("channelId")
            if channel_id:
                hits[channel_id] = hits.get(channel_id, 0) + 1
    return hits


def _sample_channels(
    api: DataApi,
    conn: sqlite3.Connection,
    niche_id: int,
    channel_ids: Sequence[str],
    now: datetime,
    cfg: ValidationConfig,
    report: NicheReport,
) -> dict[str, str | None]:
    """``channels.list`` in batches: upsert, snapshot, link. Returns ``{id: uploads}``."""
    uploads: dict[str, str | None] = {}
    for batch in batches(list(channel_ids)):
        response = api.channels(batch)
        with conn:
            for item in response.get("items", []):
                snippet = item.get("snippet") or {}
                stats = item.get("statistics") or {}
                related = (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
                created = snippet.get("publishedAt")
                subs = (
                    None
                    if stats.get("hiddenSubscriberCount")
                    else to_int(stats.get("subscriberCount"))
                )
                repo.upsert_channel(
                    conn,
                    item["id"],
                    role="niche_sample",
                    title=snippet.get("title"),
                    custom_url=snippet.get("customUrl"),
                    country=snippet.get("country"),
                    created_at=created,
                    uploads_playlist_id=related.get("uploads"),
                    last_refreshed=to_utc_iso(now),
                )
                repo.add_channel_snapshot(
                    conn,
                    item["id"],
                    subs=subs,
                    view_count=to_int(stats.get("viewCount")),
                    video_count=to_int(stats.get("videoCount")),
                    captured_at=now,
                )
                small = is_small(subs, parse_dt(created) if created else None, now, cfg)
                repo.put_niche_channel(conn, niche_id, item["id"], is_small=small, added_at=now)
                report.sampled += 1
                report.small += bool(small)
                uploads[item["id"]] = related.get("uploads")
    return uploads


def _sample_videos(
    api: DataApi,
    conn: sqlite3.Connection,
    channel_id: str,
    uploads: str,
    cfg: ValidationConfig,
    shorts_max_seconds: int,
    report: NicheReport,
) -> None:
    """One playlist page, then one ``videos.list`` for the newest ``videos_per_channel``."""
    ids: list[str] = []
    for video_id, _published in upload_page(api.playlist_items(uploads)):
        if video_id not in ids:
            ids.append(video_id)
    ids = ids[: cfg.videos_per_channel]
    if not ids:
        return
    response = api.videos(ids)
    with conn:
        for item in response.get("items", []):
            store_video(conn, item, channel_id, shorts_max_seconds)
            report.videos += 1


def validate_niche(
    api: DataApi,
    conn: sqlite3.Connection,
    niche: Mapping[str, Any],
    *,
    cfg: ValidationConfig,
    shorts_max_seconds: int,
    now: datetime,
    report: NicheReport | None = None,
) -> NicheReport:
    """Validate one niche, filling ``report`` (or a new one) as it goes.

    ``QuotaExhausted`` propagates with the niche left as it was and ``report`` counting
    what was spent before the stop.
    """
    report = report if report is not None else new_report(niche)
    specs = planned_searches(niche)
    if not specs:
        report.skipped = "no search queries"
        return report
    before = api.ledger.run_used
    try:
        hits = _search(api, specs, now - timedelta(days=cfg.lookback_days), cfg, report)
        report.distinct_channels = len(hits)
        chosen = rank_channels(hits, cfg.max_channels)
        uploads = _sample_channels(api, conn, report.niche_id, chosen, now, cfg, report)
        for channel_id in chosen:
            playlist = uploads.get(channel_id)
            if playlist:
                _sample_videos(api, conn, channel_id, playlist, cfg, shorts_max_seconds, report)
        with conn:
            repo.mark_niche_validated(conn, report.niche_id, now)
        report.validated = True
    finally:
        report.units = api.ledger.run_used - before
    return report


def headroom(api: DataApi) -> int:
    """Units this run may still spend: the smaller of today's and ``--max-units``'s rest."""
    run_left = api.ledger.remaining_run()
    today_left = api.ledger.remaining_today()
    return today_left if run_left is None else min(today_left, run_left)


def validate_niches(
    api: DataApi,
    conn: sqlite3.Connection,
    niches: Sequence[Mapping[str, Any]],
    *,
    cfg: ValidationConfig,
    shorts_max_seconds: int,
    now: datetime,
) -> ValidateResult:
    """Validate ``niches`` in order; stop cleanly on the first quota stop.

    A niche starts only if ``worst_case_units`` fits the headroom; otherwise the run stops
    with a ``QuotaExhausted`` in ``result.stopped`` and that niche in ``not_started``.
    """
    result = ValidateResult()
    for niche in niches:
        report = new_report(niche)
        need = worst_case_units(cfg, len(planned_searches(niche)))
        left = headroom(api)
        if left < need:
            ledger = api.ledger
            run_left = ledger.remaining_run()
            if run_left is not None and run_left <= ledger.remaining_today():
                result.stopped = QuotaExhausted("run", ledger.run_used, ledger.run_cap)
            else:
                result.stopped = QuotaExhausted("daily", ledger.used_today(), ledger.daily_cap)
            result.not_started = report
            return result
        try:
            validate_niche(
                api,
                conn,
                niche,
                cfg=cfg,
                shorts_max_seconds=shorts_max_seconds,
                now=now,
                report=report,
            )
        except QuotaExhausted as exc:
            result.reports.append(report)
            result.stopped = exc
            return result
        result.reports.append(report)
    return result


def format_report(report: NicheReport) -> str:
    head = f"niche {report.niche_id} [{report.fmt}] {report.label}"
    if report.skipped:
        return f"{head}: skipped ({report.skipped})"
    state = "validated" if report.validated else "left proposed (stopped mid-niche)"
    early = " (early stop)" if report.early_stop else ""
    return (
        f"{head}: {state}; {report.searches} searches{early}, "
        f"{report.distinct_channels} distinct channels, {report.sampled} sampled "
        f"({report.small} small), {report.videos} videos; {report.units} units"
    )
