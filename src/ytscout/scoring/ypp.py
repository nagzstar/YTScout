"""DESIGN.md §6.6: months until a newcomer in a niche qualifies for the YouTube Partner
Programme (YPP), the point at which AdSense starts. Pure: takes the same ``NicheSample``
and ``cfg`` (``niche_scoring`` from ``config/scoring.yaml``) as ``opportunity.py``, no DB.

The thresholds live under ``niche_scoring.ypp`` (issue 054, checked against YouTube Help
"YouTube Partner Program overview & eligibility"): ``subs_min`` for both formats, and then
per format either ``views_90d`` public Shorts views in a rolling 90-day window or
``watch_hours_12mo`` public watch hours in a rolling 12-month window.

The newcomer model
------------------
A "good newcomer" is the 051 anchor: the ``newcomer_views_percentile``-th percentile of
the active small channels (those that published in-format inside the window), both for
monthly views (``opportunity.newcomer_anchor_views``) and for ``subs_per_month``
(the same percentile of each active small channel's ``subs ÷ age in months``).

The newcomer starts at zero and its monthly rate rises linearly to the anchor rate ``r``
over ``ramp_months`` ``R``, then holds. Units accumulated by month ``t``::

    C(t) = r × t² / (2R)      for 0 ≤ t ≤ R
    C(t) = r × (t − R/2)      for t ≥ R

A rolling window of ``w`` months holds ``W(t) = C(t) − C(t − w)``, which is ``w × r`` once
the ramp is behind it (``t ≥ R + w``). So:

- ``months_subs``  = smallest ``t`` with ``C(t) ≥ subs_min``. Past the ramp that is
  ``subs_min ÷ subs_per_month + R/2``; with ``R = 0`` it is the issue's plain
  ``subs_min ÷ subs_per_month``.
- ``months_views`` (Shorts)    = smallest ``t`` with ``W(t) ≥ views_90d``, ``w = 3``;
  reachable only when ``3 × monthly_views ≥ views_90d``.
- ``months_views`` (long-form) = smallest ``t`` with ``W(t) ≥ watch_hours_12mo``,
  ``w = 12``, where the monthly rate is
  ``watch_hours_per_month = monthly_views × median_duration_s × retention_share ÷ 3600``;
  reachable only when ``12 × watch_hours_per_month ≥ watch_hours_12mo``.
- ``months`` = ``max(months_subs, months_views)`` when both are reachable, else ``None``
  with ``reachable = False`` and a flag saying why.

``months_until`` finds ``t`` by bisection on the monotone totals to 1e-4 of a month.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ytscout.scoring.metrics import percentile
from ytscout.scoring.opportunity import (
    DAYS_PER_MONTH,
    _num,
    _sub,
    active_small_channels,
    newcomer_anchor_views,
    window_videos,
)
from ytscout.scoring.types import NicheSample

# Flags. The dashboard shows them as badges (money.FLAG_LABELS has the reader-facing text).
UNREACHABLE_AT_RATE = "ypp_unreachable_at_rate"  # a rate is known, but the window never fills
NO_NEWCOMER_RATE = "ypp_no_newcomer_rate"  # no active small channel to take a rate from
NO_DURATION = "ypp_no_duration"  # long-form: no video durations to turn views into hours

FLAG_LABELS: dict[str, str] = {
    UNREACHABLE_AT_RATE: "YPP unreachable at a good newcomer's rate",
    NO_NEWCOMER_RATE: "YPP unknown: no active small channel",
    NO_DURATION: "YPP unknown: no video durations",
}

# The rolling windows YouTube's two thresholds are measured over, in months. They are part
# of the rule's definition (the config keys are named after them), not tunables.
SHORTS_WINDOW_MONTHS = 3.0  # views_90d
LONGFORM_WINDOW_MONTHS = 12.0  # watch_hours_12mo
SECONDS_PER_HOUR = 3600.0
_PRECISION_MONTHS = 1e-4


@dataclass(frozen=True)
class YppEstimate:
    """What ``months_to_ypp`` returns. ``months`` is ``None`` unless ``reachable``; the
    per-threshold months stay filled where they could be computed so the reader can see
    which threshold is the wall. The rates are reported for the docstring's formulas."""

    months_subs: float | None
    months_views: float | None
    months: float | None
    reachable: bool
    flags: list[str] = field(default_factory=list)
    subs_per_month: float | None = None
    monthly_views: float | None = None
    watch_hours_per_month: float | None = None


# ---------------------------------------------------------------- the ramp model


def cumulative(rate: float, ramp: float, t: float) -> float:
    """``C(t)``: units accumulated by month ``t`` by a newcomer whose monthly rate rises
    linearly from 0 to ``rate`` over ``ramp`` months, then holds. 0 for ``t ≤ 0``."""
    if t <= 0:
        return 0.0
    if t <= ramp:
        return rate * t * t / (2.0 * ramp)
    return rate * (t - ramp / 2.0)


def window_total(rate: float, ramp: float, t: float, window: float) -> float:
    """``W(t)``: units inside the rolling ``window`` months ending at month ``t``."""
    return cumulative(rate, ramp, t) - cumulative(rate, ramp, t - window)


def months_until(total: Callable[[float], float], target: float, upper: float) -> float | None:
    """The smallest ``t`` in ``[0, upper]`` with ``total(t) ≥ target`` for a non-decreasing
    ``total``, by bisection to ``_PRECISION_MONTHS``; ``None`` when ``total(upper)`` falls
    short (then no later month reaches it either, by construction of ``upper``)."""
    if target <= 0:
        return 0.0
    if total(upper) < target:
        return None
    low, high = 0.0, upper
    while high - low > _PRECISION_MONTHS:
        mid = (low + high) / 2.0
        if total(mid) >= target:
            high = mid
        else:
            low = mid
    return high


# ---------------------------------------------------------------- newcomer rates


def subs_per_month(sample: NicheSample, cfg: Mapping[str, Any]) -> float | None:
    """The ``newcomer_views_percentile``-th percentile over the active small channels of
    ``subs ÷ age in months``, age floored at ``ypp.age_floor_months`` so a week-old channel
    with a few hundred subs does not set the pace. ``None`` with no active small channel.
    """
    floor = _num(_sub(cfg, "ypp"), "age_floor_months")
    rates: list[float] = []
    for channel in active_small_channels(sample, cfg):
        # Small channels have both facts by definition (opportunity.is_small).
        assert channel.subs is not None and channel.created_at is not None
        age_months = max((sample.now - channel.created_at).days / DAYS_PER_MONTH, floor)
        rates.append(channel.subs / age_months)
    return percentile(rates, _num(cfg, "newcomer_views_percentile") / 100.0)


def median_duration_s(sample: NicheSample, cfg: Mapping[str, Any]) -> float | None:
    """Median duration of the active small channels' in-format videos inside the window:
    the length a newcomer's videos would have. ``None`` when there are none."""
    durations = [
        v.duration_s
        for channel in active_small_channels(sample, cfg)
        for v in window_videos(channel, sample, cfg)
        if v.duration_s is not None
    ]
    return float(statistics.median(durations)) if durations else None


def watch_hours_per_month(monthly_views: float, duration_s: float, retention_share: float) -> float:
    """``monthly_views × duration_s × retention_share ÷ 3600`` (§6.6)."""
    return monthly_views * duration_s * retention_share / SECONDS_PER_HOUR


# ---------------------------------------------------------------- the estimate


def months_to_ypp(sample: NicheSample, cfg: Mapping[str, Any]) -> YppEstimate:
    """§6.6: months until a good newcomer in this niche × format meets both YPP
    thresholds under the ramp model in the module docstring."""
    ypp = _sub(cfg, "ypp")
    ramp = _num(ypp, "ramp_months")
    subs_rate = subs_per_month(sample, cfg)
    views_rate = newcomer_anchor_views(sample, cfg)
    if subs_rate is None or views_rate is None:
        return YppEstimate(None, None, None, False, [NO_NEWCOMER_RATE])

    flags: list[str] = []
    subs_min = _num(ypp, "subs_min")
    months_subs = (
        months_until(
            lambda t: cumulative(subs_rate, ramp, t), subs_min, ramp + subs_min / subs_rate + 1.0
        )
        if subs_rate > 0
        else None
    )

    hours: float | None = None
    if sample.fmt == "shorts":
        window, target, per_month = SHORTS_WINDOW_MONTHS, _num(ypp, "views_90d"), views_rate
    else:
        window, target = LONGFORM_WINDOW_MONTHS, _num(ypp, "watch_hours_12mo")
        duration = median_duration_s(sample, cfg)
        if duration is None:
            flags.append(NO_DURATION)
            per_month = None
        else:
            hours = watch_hours_per_month(views_rate, duration, _num(ypp, "retention_share"))
            per_month = hours
    months_views = (
        months_until(lambda t: window_total(per_month, ramp, t, window), target, ramp + window)
        if per_month
        else None
    )
    if months_subs is None or (per_month is not None and months_views is None):
        flags.append(UNREACHABLE_AT_RATE)
    reachable = months_subs is not None and months_views is not None
    return YppEstimate(
        months_subs=months_subs,
        months_views=months_views,
        months=max(months_subs, months_views) if reachable else None,
        reachable=reachable,
        flags=flags,
        subs_per_month=subs_rate,
        monthly_views=views_rate,
        watch_hours_per_month=hours,
    )
