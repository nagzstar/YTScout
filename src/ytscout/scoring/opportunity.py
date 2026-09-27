"""DESIGN.md §6.1 and §6.2: the opportunity score and a newcomer's expected views.

Every function is pure and takes ``cfg``, the ``niche_scoring:`` mapping from
``config/scoring.yaml`` (see ``scoring.config.niche_scoring_config``). No number is
hard-coded here. A niche is a format × topic pair, so every view count is filtered to
the sample's format first.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from ytscout.scoring.metrics import percentile
from ytscout.scoring.types import ChannelSample, NicheSample, VideoSample

LOW_CONFIDENCE = "low_confidence"

# Days in the "month" that monthly views (and, in ypp.py, channel ages) are expressed in.
DAYS_PER_MONTH = 30
_DAYS_PER_MONTH = DAYS_PER_MONTH


# ---------------------------------------------------------------- config access


def _num(cfg: Mapping[str, Any], key: str) -> float:
    value = cfg.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise KeyError(f"niche_scoring.{key} missing or not a number")
    return float(value)


def _sub(cfg: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = cfg.get(key)
    if not isinstance(value, Mapping):
        raise KeyError(f"niche_scoring.{key} missing or not a mapping")
    return value


# ---------------------------------------------------------------- format & window


def in_format(video: VideoSample, fmt: str, cfg: Mapping[str, Any]) -> bool:
    """A video with a known duration is a Short at or under ``shorts_max_seconds``."""
    if video.duration_s is None:
        return False
    is_short = video.duration_s <= _num(cfg, "shorts_max_seconds")
    return is_short if fmt == "shorts" else not is_short


def format_videos(channel: ChannelSample, fmt: str, cfg: Mapping[str, Any]) -> list[VideoSample]:
    """The channel's videos in ``fmt``, newest first."""
    videos = [v for v in channel.videos if in_format(v, fmt, cfg)]
    return sorted(videos, key=lambda v: v.published_at, reverse=True)


def in_window(video: VideoSample, sample: NicheSample, cfg: Mapping[str, Any]) -> bool:
    """Published within the last ``window_days`` before the sample's ``now``."""
    start = sample.now - timedelta(days=_num(cfg, "window_days"))
    return start < video.published_at <= sample.now


def window_videos(
    channel: ChannelSample, sample: NicheSample, cfg: Mapping[str, Any]
) -> list[VideoSample]:
    """The channel's in-format videos published inside the window."""
    return [v for v in format_videos(channel, sample.fmt, cfg) if in_window(v, sample, cfg)]


def window_views(channel: ChannelSample, sample: NicheSample, cfg: Mapping[str, Any]) -> int:
    """Total views of the channel's in-format videos inside the window."""
    return sum(v.views for v in window_videos(channel, sample, cfg))


# ---------------------------------------------------------------- small channels


def is_small(channel: ChannelSample, sample: NicheSample, cfg: Mapping[str, Any]) -> bool | None:
    """§6.1: subs below ``small_subs_max`` AND younger than ``small_age_days``.

    ``None`` when either fact is unknown (hidden subs, no creation date): such a channel
    counts in the niche's totals but never in the small set.
    """
    if channel.subs is None or channel.created_at is None:
        return None
    age = sample.now - channel.created_at
    return channel.subs < _num(cfg, "small_subs_max") and age < timedelta(
        days=_num(cfg, "small_age_days")
    )


def small_channels(sample: NicheSample, cfg: Mapping[str, Any]) -> list[ChannelSample]:
    """The channels that are small for certain (``is_small`` is ``True``)."""
    return [c for c in sample.channels if is_small(c, sample, cfg) is True]


# ---------------------------------------------------------------- outliers


def channel_median(videos: Sequence[VideoSample], last_n: int) -> float | None:
    """Median views of the newest ``last_n`` videos; ``None`` when there are none."""
    newest = sorted(videos, key=lambda v: v.published_at, reverse=True)[:last_n]
    if not newest:
        return None
    return float(statistics.median(v.views for v in newest))


def outlier_videos(
    channel: ChannelSample, sample: NicheSample, cfg: Mapping[str, Any]
) -> list[VideoSample]:
    """§6.1 ``outlier(video)``: in-format videos inside the window whose views are at least
    ``outlier_multiplier`` × the channel's median (its newest ``outlier_window_videos``
    in-format videos, inside the window or not) AND at least ``outlier_floor_views[fmt]``.
    """
    in_fmt = format_videos(channel, sample.fmt, cfg)
    median = channel_median(in_fmt, int(_num(cfg, "outlier_window_videos")))
    if median is None:
        return []
    threshold = _num(cfg, "outlier_multiplier") * median
    floor = _num(_sub(cfg, "outlier_floor_views"), sample.fmt)
    return [
        v for v in in_fmt if in_window(v, sample, cfg) and v.views >= threshold and v.views >= floor
    ]


def small_outlier_rate(sample: NicheSample, cfg: Mapping[str, Any]) -> float:
    """Share of small channels with at least one outlier in the window; 0 with none."""
    small = small_channels(sample, cfg)
    if not small:
        return 0.0
    hits = sum(1 for c in small if outlier_videos(c, sample, cfg))
    return hits / len(small)


# ---------------------------------------------------------------- views


def newcomer_view_share(sample: NicheSample, cfg: Mapping[str, Any]) -> float:
    """Window views of small channels ÷ window views of every channel; 0 with no views."""
    total = sum(window_views(c, sample, cfg) for c in sample.channels)
    if total == 0:
        return 0.0
    small = sum(window_views(c, sample, cfg) for c in small_channels(sample, cfg))
    return small / total


def concentration(sample: NicheSample, cfg: Mapping[str, Any]) -> float:
    """Share of window views held by the top ``top_n_concentration`` channels.

    1.0 (fully concentrated) when the niche has no views at all: no evidence of room.
    """
    views = sorted((window_views(c, sample, cfg) for c in sample.channels), reverse=True)
    total = sum(views)
    if total == 0:
        return 1.0
    return sum(views[: int(_num(cfg, "top_n_concentration"))]) / total


# ---------------------------------------------------------------- the score


def opportunity(sample: NicheSample, cfg: Mapping[str, Any]) -> tuple[float, list[str]]:
    """§6.1: the weighted sum, capped at ``low_confidence_cap`` and flagged
    ``low_confidence`` when fewer than ``low_confidence_min_small`` channels are small.
    """
    weights = _sub(cfg, "weights")
    score = (
        _num(weights, "small_outlier_rate") * small_outlier_rate(sample, cfg)
        + _num(weights, "newcomer_view_share") * newcomer_view_share(sample, cfg)
        + _num(weights, "inverse_concentration") * (1.0 - concentration(sample, cfg))
    )
    flags: list[str] = []
    if len(small_channels(sample, cfg)) < _num(cfg, "low_confidence_min_small"):
        flags.append(LOW_CONFIDENCE)
        score = min(score, _num(cfg, "low_confidence_cap"))
    return score, flags


def active_small_channels(sample: NicheSample, cfg: Mapping[str, Any]) -> list[ChannelSample]:
    """§6.2 (051): the small channels that published at least ``newcomer_min_window_videos``
    in-format videos inside the window. A small channel that has gone quiet is not what a
    newcomer publishing on schedule would look like, so it stays out of the views set.
    """
    need = _num(cfg, "newcomer_min_window_videos")
    return [c for c in small_channels(sample, cfg) if len(window_videos(c, sample, cfg)) >= need]


def _active_monthly_views(sample: NicheSample, cfg: Mapping[str, Any]) -> list[float]:
    months = _num(cfg, "window_days") / _DAYS_PER_MONTH
    return [window_views(c, sample, cfg) / months for c in active_small_channels(sample, cfg)]


def newcomer_monthly_views(
    sample: NicheSample, cfg: Mapping[str, Any]
) -> tuple[float, float, float] | None:
    """§6.2: (p25, p50, p75) of the active small channels' monthly views, where monthly
    views are window views scaled to 30 days. Linearly interpolated quantiles; ``None``
    when the niche has no active small channel. The band the dashboard shows.
    """
    monthly = _active_monthly_views(sample, cfg)
    if not monthly:
        return None
    p25, p50, p75 = (percentile(monthly, q) for q in (0.25, 0.5, 0.75))
    assert p25 is not None and p50 is not None and p75 is not None
    return p25, p50, p75


def newcomer_anchor_views(sample: NicheSample, cfg: Mapping[str, Any]) -> float | None:
    """§6.2 (051): the monthly views the £ estimate anchors on, "a good newcomer": the
    ``newcomer_views_percentile``-th percentile of the same active set. ``None`` when the
    niche has no active small channel.
    """
    q = _num(cfg, "newcomer_views_percentile") / 100.0
    return percentile(_active_monthly_views(sample, cfg), q)
