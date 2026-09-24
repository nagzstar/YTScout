"""``collect --analytics``: the own channel's YouTube Analytics over a window.

Three kinds of query, all against ``channel==MINE``:

1. per-video totals, ≤ 200 ids per query (``filters=video==a,b,c``) → ``own_analytics``;
2. channel-level per-day views, revenue and monetised playbacks → ``own_daily``;
3. channel-level views per traffic source → ``own_traffic``.

Impressions and CTR are asked for with the first batch; if the API rejects them the batch
is retried without them and no later batch asks again (they are stored as NULL).
Revenue and RPM are written to SQLite and never printed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ytscout.store import repo
from ytscout.youtube.analytics import (
    DAILY_METRICS,
    IMPRESSION_METRICS,
    TRAFFIC_METRICS,
    VIDEO_METRICS,
    AnalyticsApi,
    QueryRejected,
)

VIDEO_BATCH = 200  # the API's maxResults ceiling for video-dimension reports
DEFAULT_DAYS = 400


@dataclass
class AnalyticsCounts:
    videos: int = 0
    days: int = 0
    traffic_sources: int = 0
    queries: int = 0
    # None until the first per-video batch answers; then whether impressions/CTR came back.
    impressions_available: bool | None = None


def window(days: int, today: date) -> tuple[str, str]:
    """``(start, end)`` as ``YYYY-MM-DD``: ``days`` days ending yesterday, inclusive."""
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def rpm_usd(revenue_usd: float | None, views: int | None) -> float | None:
    """Revenue per 1,000 views; ``None`` without revenue or with no views."""
    if revenue_usd is None or not views:
        return None
    return revenue_usd / views * 1000


def batches(ids: Sequence[str], size: int = VIDEO_BATCH) -> list[list[str]]:
    return [list(ids[i : i + size]) for i in range(0, len(ids), size)]


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _video_values(row: dict[str, Any]) -> dict[str, Any]:
    views = _int(row.get("views"))
    revenue = _float(row.get("estimatedRevenue"))
    gained, lost = _int(row.get("subscribersGained")), _int(row.get("subscribersLost"))
    return {
        "views": views,
        "est_revenue_usd": revenue,
        "rpm_usd": rpm_usd(revenue, views),
        "avg_view_duration_s": _float(row.get("averageViewDuration")),
        "avg_view_pct": _float(row.get("averageViewPercentage")),
        "impressions": _int(row.get("impressions")),
        "ctr": _float(row.get("impressionsClickThroughRate")),
        "sub_delta": None if gained is None or lost is None else gained - lost,
        "minutes_watched": _float(row.get("estimatedMinutesWatched")),
        "likes": _int(row.get("likes")),
    }


def video_query(ids: Sequence[str], start: str, end: str, *, impressions: bool) -> dict:
    metrics = (*VIDEO_METRICS, *IMPRESSION_METRICS) if impressions else VIDEO_METRICS
    return {
        "startDate": start,
        "endDate": end,
        "metrics": ",".join(metrics),
        "dimensions": "video",
        "filters": "video==" + ",".join(ids),
        "sort": "-views",
        "maxResults": VIDEO_BATCH,
    }


def daily_query(start: str, end: str) -> dict:
    return {
        "startDate": start,
        "endDate": end,
        "metrics": ",".join(DAILY_METRICS),
        "dimensions": "day",
        "sort": "day",
    }


def traffic_query(start: str, end: str) -> dict:
    return {
        "startDate": start,
        "endDate": end,
        "metrics": ",".join(TRAFFIC_METRICS),
        "dimensions": "insightTrafficSourceType",
    }


def collect_analytics(
    api: AnalyticsApi,
    conn: sqlite3.Connection,
    video_ids: Sequence[str],
    *,
    start: str,
    end: str,
) -> AnalyticsCounts:
    """Run the three query kinds and write their rows; each batch commits on its own."""
    counts = AnalyticsCounts()
    for ids in batches(video_ids):
        ask_impressions = counts.impressions_available is not False
        try:
            rows = api.query(**video_query(ids, start, end, impressions=ask_impressions))
            counts.queries += 1
            counts.impressions_available = ask_impressions
        except QueryRejected:
            if not ask_impressions:
                raise
            counts.queries += 1
            counts.impressions_available = False
            rows = api.query(**video_query(ids, start, end, impressions=False))
            counts.queries += 1
        with conn:
            for row in rows:
                repo.upsert_own_analytics(conn, row["video"], start, end, **_video_values(row))
        counts.videos += len(rows)

    rows = api.query(**daily_query(start, end))
    counts.queries += 1
    with conn:
        for row in rows:
            repo.upsert_own_daily(
                conn,
                row["day"],
                _int(row.get("views")),
                _float(row.get("estimatedRevenue")),
                _int(row.get("monetizedPlaybacks")),
            )
    counts.days = len(rows)

    rows = api.query(**traffic_query(start, end))
    counts.queries += 1
    with conn:
        for row in rows:
            repo.upsert_own_traffic(
                conn, start, end, row["insightTrafficSourceType"], _int(row.get("views"))
            )
    counts.traffic_sources = len(rows)
    return counts
