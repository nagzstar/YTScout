"""Competitor metrics (DESIGN.md §4.4): pure functions over plain dataclasses.

One call of ``channel_metrics`` covers one channel × one window × one format. Nothing
here reads the DB or ``config/scoring.yaml``: the caller passes the videos, the latest
subscriber count and a ``MetricsConfig``.

Views are the latest snapshot per video. Two approximations are worth knowing:

- **velocity** comes from weekly snapshots, so "views at 7 days" is the views of the last
  snapshot taken at or before 7 days after publish, not the exact figure. It is ``None``
  until some video in the window has been captured that early and at least once more;
  it fills in over the weeks after a channel starts being tracked.
- **monthly views** (the dashboard chart) groups videos by publish month and sums their
  latest views, so it is "views by publish month", not views earned in that month.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

FORMATS = ("shorts", "longform")
VELOCITY_DAYS = (7, 30)

_DIGIT = re.compile(r"\d")
_TOP_N = re.compile(r"\btop\s*\d+", re.IGNORECASE)
# A word of two or more letters, all upper case: "TOP", "WOW". A lone "I" or "A" is not.
_CAPS_WORD = re.compile(r"\b[A-Z]{2,}\b")


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    views: int | None


@dataclass(frozen=True)
class VideoStats:
    """One video and its snapshots (any order; sorted where it matters)."""

    id: str
    title: str | None
    published_at: datetime
    duration_s: int | None
    is_short: bool | None
    snapshots: tuple[Snapshot, ...] = ()

    @property
    def latest_views(self) -> int | None:
        dated = [s for s in self.snapshots if s.views is not None]
        return max(dated, key=lambda s: s.captured_at).views if dated else None


@dataclass(frozen=True)
class LengthBucket:
    """Durations at or under ``max_seconds`` (above the previous bucket's); ``None``: no top."""

    label: str
    max_seconds: int | None


@dataclass(frozen=True)
class MetricsConfig:
    """The ``competitor_metrics:`` section of scoring.yaml."""

    outlier_multiplier: float
    outlier_window_videos: int
    length_buckets: tuple[LengthBucket, ...]


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default); ``None`` if empty."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def in_format(video: VideoStats, fmt: str) -> bool:
    """Shorts are ``is_short``; long-form is ``not is_short``; unknown is neither."""
    if fmt not in FORMATS:
        raise ValueError(f"fmt must be one of {FORMATS}, got {fmt!r}")
    if video.is_short is None:
        return False
    return video.is_short if fmt == "shorts" else not video.is_short


def bucket_counts(durations: Sequence[int], buckets: Sequence[LengthBucket]) -> dict[str, int]:
    """Count each duration into the first bucket whose ``max_seconds`` it does not exceed."""
    counts = {b.label: 0 for b in buckets}
    for seconds in durations:
        for b in buckets:
            if b.max_seconds is None or seconds <= b.max_seconds:
                counts[b.label] += 1
                break
    return counts


def title_features(titles: Sequence[str]) -> dict[str, float | None]:
    """Mean length in characters and the share of titles with each pattern."""
    n = len(titles)
    if n == 0:
        return {
            "mean_length": None,
            "share_number": None,
            "share_question": None,
            "share_top_n": None,
            "share_caps_word": None,
        }
    return {
        "mean_length": sum(len(t) for t in titles) / n,
        "share_number": sum(1 for t in titles if _DIGIT.search(t)) / n,
        "share_question": sum(1 for t in titles if "?" in t) / n,
        "share_top_n": sum(1 for t in titles if _TOP_N.search(t)) / n,
        "share_caps_word": sum(1 for t in titles if _CAPS_WORD.search(t)) / n,
    }


def views_at_age(video: VideoStats, days: int) -> int | None:
    """Views of the last snapshot taken at or before ``days`` after publish."""
    limit = video.published_at + timedelta(days=days)
    early = [s for s in video.snapshots if s.views is not None and s.captured_at <= limit]
    return max(early, key=lambda s: s.captured_at).views if early else None


def velocity(videos: Sequence[VideoStats]) -> dict[str, Any]:
    """Median views at 7 and 30 days, over videos with ≥ 2 snapshots captured early enough."""
    result: dict[str, Any] = {}
    for days in VELOCITY_DAYS:
        found = [
            v
            for v in (views_at_age(video, days) for video in videos if len(video.snapshots) >= 2)
            if v is not None
        ]
        result[f"d{days}"] = _median(found)
        result[f"n{days}"] = len(found)
    return result


def channel_metrics(
    videos: Sequence[VideoStats],
    *,
    window_days: int,
    fmt: str,
    now: datetime,
    subs: int | None,
    config: MetricsConfig,
) -> dict[str, Any]:
    """§4.4 metrics for one channel's videos published in the last ``window_days``.

    ``share_of_tracked_views`` needs every channel, so it is ``None`` here and filled in
    by ``add_view_shares``; ``window_views`` is what it divides.
    """
    formatted = [v for v in videos if in_format(v, fmt) and v.published_at <= now]
    start = now - timedelta(days=window_days)
    window = [v for v in formatted if v.published_at >= start]
    views = [v for v in (video.latest_views for video in window) if v is not None]
    median = _median(views)

    # The outlier bar is the median of the channel's newest N videos in this format, so a
    # quiet window does not make every video an outlier.
    newest = sorted(formatted, key=lambda v: (v.published_at, v.id), reverse=True)
    recent_views = [
        v
        for v in (video.latest_views for video in newest[: config.outlier_window_videos])
        if v is not None
    ]
    baseline = _median(recent_views)
    outlier_ids: list[str] = []
    if baseline is not None and baseline > 0:
        bar = config.outlier_multiplier * baseline
        outlier_ids = [
            video.id
            for video in sorted(window, key=lambda v: (v.published_at, v.id))
            if video.latest_views is not None and video.latest_views >= bar
        ]

    return {
        "window_days": window_days,
        "format": fmt,
        "video_count": len(window),
        "uploads_per_week": len(window) / (window_days / 7),
        "window_views": sum(views),
        "views_median": median,
        "views_p25": percentile(views, 0.25),
        "views_p75": percentile(views, 0.75),
        "views_max": max(views) if views else None,
        "subs": subs,
        "views_per_sub": median / subs if median is not None and subs else None,
        "outlier_baseline": baseline,
        "outlier_count": len(outlier_ids),
        "outlier_ids": outlier_ids,
        "length_buckets": bucket_counts(
            [v.duration_s for v in window if v.duration_s is not None], config.length_buckets
        ),
        "title_features": title_features([v.title for v in window if v.title is not None]),
        "velocity": velocity(window),
        "share_of_tracked_views": None,
    }


def add_view_shares(metrics_by_channel: Mapping[str, dict[str, Any]]) -> None:
    """Set ``share_of_tracked_views`` on each channel's metrics (same window and format).

    Shares sum to 1 across channels; all ``None`` when no tracked channel has views.
    """
    total = sum(m["window_views"] for m in metrics_by_channel.values())
    for m in metrics_by_channel.values():
        m["share_of_tracked_views"] = m["window_views"] / total if total else None


def top_bucket(buckets: Mapping[str, int]) -> str | None:
    """The label with the most videos (the first on a tie); ``None`` if all are empty."""
    best = max(buckets.items(), key=lambda kv: kv[1], default=None)
    return best[0] if best and best[1] > 0 else None


def month_keys(now: datetime, months: int) -> list[str]:
    """``YYYY-MM`` for the ``months`` calendar months ending with ``now``'s, oldest first."""
    year, month = now.year, now.month
    keys: list[str] = []
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return keys[::-1]


def monthly_views(videos: Sequence[VideoStats], *, now: datetime, months: int = 12) -> dict:
    """Latest views summed by publish month for the last ``months`` months (0 if none)."""
    totals = dict.fromkeys(month_keys(now, months), 0)
    for video in videos:
        key = f"{video.published_at.year:04d}-{video.published_at.month:02d}"
        views = video.latest_views
        if key in totals and views is not None:
            totals[key] += views
    return totals
