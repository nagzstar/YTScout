"""DESIGN.md §6.6 (054): months to Partner Programme monetisation, hand-worked.

Config is test_scoring's ``CFG`` (subs_min 1,000; Shorts 10M views / 90 d; long-form 4,000
watch hours / 12 mo; retention 0.4; ramp 3 months; age floor 1 month), ``now = 2026-09-01``.

The newcomer rate examples below use one active small channel ``N``: 900 subs, created
2026-06-03 (90 days → 3.0 months → **300 subs/month**), and window views chosen per case.
With ramp ``R = 3``: ``C(t) = 300·t²/6 = 50·t²`` for ``t ≤ 3`` (450 at ``t = 3``), then
``300·(t − 1.5)``; ``1,000 = 300·(t − 1.5)`` → ``months_subs = 4.8333``.

Shorts, reachable: 12,000,000 window views → 4,000,000/month; ``3 × 4M = 12M ≥ 10M``. Under
the ramp, ``W(t) = C(t) − C(t − 3)``: for ``t ≤ 3`` at most ``4M·9/6 = 6M < 10M``; for
``3 ≤ t ≤ 6``, ``4M·(t − 1.5) − 4M·(t − 3)²/6 = 10M`` → with ``u = t − 3``:
``u² − 6u + 6 = 0`` → ``u = 3 − √3 = 1.2679`` → ``months_views = 4.2679``;
``months = max(4.8333, 4.2679) = 4.83``.

Shorts, unreachable: 300,000 window views → 100,000/month; ``3 × 100k < 10M``.

Long-form, reachable: 300,000 window views of 600 s videos → 100,000/month →
``100,000 × 600 × 0.4 ÷ 3600 = 6,666.67 h/month``; ``12 × 6,666.67 ≥ 4,000``. Within the
ramp ``W(t) = C(t) = 6,666.67·t²/6 = 1,111.1·t² = 4,000`` → ``t = √3.6 = 1.8974``;
``months = max(4.8333, 1.8974) = 4.83`` (subs are the wall).

Long-form, unreachable: 3,000 window views of 300 s videos → 1,000/month → 33.3 h/month;
``12 × 33.3 = 400 < 4,000``.

The 023 example (``SAMPLE``): active small A, B, C; ages 184, 229, 304 days → 6.133,
7.633, 10.133 months → 326.09, 655.02, 888.16 subs/month; p75 = 655.02 + 0.5 × 233.14 =
771.59. ``C(3) = 1,157 ≥ 1,000`` so within the ramp: ``771.59·t²/6 = 1,000`` →
``t = 2.7886``. Views: p75 = 26,667/month, ``3 × 26,667 = 80k < 10M`` → unreachable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from test_scoring import CFG, NOW, SAMPLE, sig3

from ytscout.scoring import ScoringConfigError, niche_scoring_config
from ytscout.scoring.types import ChannelSample, NicheSample, VideoSample
from ytscout.scoring.ypp import (
    NO_DURATION,
    NO_NEWCOMER_RATE,
    UNREACHABLE_AT_RATE,
    cumulative,
    median_duration_s,
    months_to_ypp,
    months_until,
    subs_per_month,
    watch_hours_per_month,
    window_total,
)

CREATED = datetime(2026, 6, 3, tzinfo=UTC)  # 90 days before NOW


def newcomer(window_views: int, *, duration_s: int, n_videos: int = 6) -> ChannelSample:
    """One active small channel: 900 subs, 3 months old, ``window_views`` spread evenly."""
    each = window_views // n_videos
    return ChannelSample(
        channel_id="N",
        subs=900,
        created_at=CREATED,
        videos=[
            VideoSample(
                views=each, published_at=NOW - timedelta(days=7 * (i + 1)), duration_s=duration_s
            )
            for i in range(n_videos)
        ],
    )


def sample(channel: ChannelSample, fmt: str) -> NicheSample:
    return NicheSample(channels=[channel], fmt=fmt, now=NOW)


# ---------------------------------------------------------------- the ramp model


def test_cumulative_ramps_then_holds() -> None:
    assert cumulative(300, 3, 0) == 0
    assert cumulative(300, 3, -1) == 0
    assert cumulative(300, 3, 3) == 450  # r·R/2
    assert cumulative(300, 3, 4.8333333) == pytest.approx(1000, abs=0.01)
    assert cumulative(300, 0, 2) == 600  # no ramp: r·t


def test_window_total_settles_at_window_times_rate() -> None:
    assert window_total(4_000_000, 3, 6, 3) == pytest.approx(12_000_000)
    assert window_total(4_000_000, 3, 100, 3) == pytest.approx(12_000_000)
    assert window_total(4_000_000, 3, 2, 3) == cumulative(4_000_000, 3, 2)


def test_months_until_bisects_and_gives_up_past_upper() -> None:
    assert months_until(lambda t: 50 * t * t, 450, 10) == pytest.approx(3.0, abs=1e-3)
    assert months_until(lambda t: 50 * t * t, 0, 10) == 0.0
    assert months_until(lambda t: 50 * t * t, 10_000, 10) is None


# ---------------------------------------------------------------- newcomer rates


def test_subs_per_month_is_subs_over_age_at_the_percentile() -> None:
    assert subs_per_month(sample(newcomer(1, duration_s=45), "shorts"), CFG) == pytest.approx(300)
    assert sig3(subs_per_month(SAMPLE, CFG)) == 772
    assert subs_per_month(NicheSample(channels=[], fmt="shorts", now=NOW), CFG) is None


def test_subs_per_month_floors_the_age() -> None:
    week_old = ChannelSample(
        channel_id="W",
        subs=200,
        created_at=NOW - timedelta(days=7),
        videos=[VideoSample(views=10, published_at=NOW - timedelta(days=1), duration_s=30)],
    )
    # 7 days = 0.233 months → 857/month unfloored; the 1-month floor says 200/month.
    assert subs_per_month(sample(week_old, "shorts"), CFG) == pytest.approx(200)


def test_median_duration_and_watch_hours() -> None:
    assert median_duration_s(sample(newcomer(600, duration_s=600), "longform"), CFG) == 600
    assert median_duration_s(sample(newcomer(600, duration_s=45), "longform"), CFG) is None
    assert watch_hours_per_month(100_000, 600, 0.4) == pytest.approx(6_666.67, abs=0.01)


# ---------------------------------------------------------------- hand-worked estimates


def test_shorts_reachable() -> None:
    est = months_to_ypp(sample(newcomer(12_000_000, duration_s=45), "shorts"), CFG)
    assert est.reachable and est.flags == []
    assert sig3(est.months_subs) == 4.83
    assert sig3(est.months_views) == 4.27
    assert sig3(est.months) == 4.83
    assert est.subs_per_month == pytest.approx(300)
    assert est.monthly_views == pytest.approx(4_000_000)
    assert est.watch_hours_per_month is None


def test_shorts_unreachable_at_rate() -> None:
    est = months_to_ypp(sample(newcomer(300_000, duration_s=45), "shorts"), CFG)
    assert not est.reachable and est.months is None and est.months_views is None
    assert est.flags == [UNREACHABLE_AT_RATE]
    assert sig3(est.months_subs) == 4.83  # subs alone would have been fine


def test_longform_reachable_subs_are_the_wall() -> None:
    est = months_to_ypp(sample(newcomer(300_000, duration_s=600), "longform"), CFG)
    assert est.reachable and est.flags == []
    assert sig3(est.watch_hours_per_month) == 6_670
    assert sig3(est.months_views) == 1.90
    assert sig3(est.months_subs) == 4.83
    assert sig3(est.months) == 4.83


def test_longform_unreachable_at_rate() -> None:
    est = months_to_ypp(sample(newcomer(3_000, duration_s=300), "longform"), CFG)
    assert not est.reachable and est.months is None
    assert sig3(est.watch_hours_per_month) == 33.3
    assert est.flags == [UNREACHABLE_AT_RATE]


def test_023_example_is_unreachable_with_subs_in_reach() -> None:
    est = months_to_ypp(SAMPLE, CFG)
    assert not est.reachable and est.months is None
    assert sig3(est.months_subs) == 2.79
    assert sig3(est.monthly_views) == 26_700
    assert est.flags == [UNREACHABLE_AT_RATE]


def test_no_active_small_channel_means_no_rate() -> None:
    big = ChannelSample(channel_id="B", subs=1_000_000, created_at=CREATED, videos=[])
    est = months_to_ypp(sample(big, "shorts"), CFG)
    assert est == months_to_ypp(NicheSample(channels=[], fmt="shorts", now=NOW), CFG)
    assert not est.reachable and est.months_subs is None and est.flags == [NO_NEWCOMER_RATE]


def test_longform_without_durations_is_flagged() -> None:
    channel = newcomer(300_000, duration_s=600)
    undated = ChannelSample(
        channel_id=channel.channel_id,
        subs=channel.subs,
        created_at=channel.created_at,
        videos=[
            VideoSample(views=v.views, published_at=v.published_at, duration_s=None)
            for v in channel.videos
        ],
    )
    # Videos with no duration are in no format, so nothing is in-format: no active channel.
    assert months_to_ypp(sample(undated, "longform"), CFG).flags == [NO_NEWCOMER_RATE]
    # An active channel (min window videos 0) whose in-window videos still have no duration.
    cfg = {**CFG, "newcomer_min_window_videos": 0}
    est = months_to_ypp(sample(undated, "longform"), cfg)
    assert est.flags == [NO_DURATION] and not est.reachable and est.months_views is None


def test_zero_ramp_is_the_plain_division() -> None:
    cfg = {**CFG, "ypp": {**CFG["ypp"], "ramp_months": 0}}
    est = months_to_ypp(sample(newcomer(12_000_000, duration_s=45), "shorts"), cfg)
    assert est.months_subs == pytest.approx(1000 / 300, abs=1e-3)
    assert est.months_views == pytest.approx(10 / 4, abs=1e-3)  # 10M ÷ 4M a month


def test_zero_subs_rate_is_unreachable() -> None:
    channel = newcomer(12_000_000, duration_s=45)
    unsubscribed = ChannelSample(
        channel_id="Z", subs=0, created_at=channel.created_at, videos=channel.videos
    )
    est = months_to_ypp(sample(unsubscribed, "shorts"), CFG)
    assert not est.reachable and est.months_subs is None and est.flags == [UNREACHABLE_AT_RATE]
    assert sig3(est.months_views) == 4.27


# ---------------------------------------------------------------- config


def _doc(**ypp: object) -> dict:
    return {
        "shorts_max_seconds": 180,
        "niche_scoring": {
            **{k: v for k, v in CFG.items() if k not in ("shorts_max_seconds", "ypp")},
            "ypp": {**CFG["ypp"], "source": "https://example", "last_reviewed": "x", **ypp},
        },
    }


def test_config_reads_ypp_and_ignores_the_provenance_keys() -> None:
    assert niche_scoring_config(_doc())["ypp"] == CFG["ypp"]


@pytest.mark.parametrize(
    "bad",
    [
        {"retention_share": 1.5},
        {"views_90d": 0},
        {"subs_min": 0},
        {"ramp_months": -1},
        {"age_floor_months": 0},
        {"watch_hours_12mo": "4000"},
    ],
)
def test_config_rejects_bad_ypp_values(bad: dict) -> None:
    with pytest.raises(ScoringConfigError, match="niche_scoring.ypp"):
        niche_scoring_config(_doc(**bad))


def test_config_requires_the_ypp_mapping() -> None:
    doc = _doc()
    del doc["niche_scoring"]["ypp"]
    with pytest.raises(ScoringConfigError, match="ypp"):
        niche_scoring_config(doc)
