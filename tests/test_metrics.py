"""§4.4 metrics against a hand-worked fixture: 2 channels × 8 videos.

Every expected number below was worked out by hand from the table in each channel's
fixture; the working is in the comments.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ytscout.scoring import ScoringConfigError, load_scoring, metrics_config
from ytscout.scoring.metrics import (
    LengthBucket,
    MetricsConfig,
    Snapshot,
    VideoStats,
    add_view_shares,
    channel_metrics,
    monthly_views,
    percentile,
    top_bucket,
    velocity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 30, tzinfo=UTC)
CONFIG = MetricsConfig(
    outlier_multiplier=3.0,
    outlier_window_videos=30,
    length_buckets=(
        LengthBucket("≤30s", 30),
        LengthBucket("30–60s", 60),
        LengthBucket("60–180s", 180),
        LengthBucket("3–8min", 480),
        LengthBucket("8–20min", 1200),
        LengthBucket("20min+", None),
    ),
)


def video(vid: str, days_ago: int, views: int, seconds: int, short: bool, title: str):
    return VideoStats(
        id=vid,
        title=title,
        published_at=NOW - timedelta(days=days_ago),
        duration_s=seconds,
        is_short=short,
        snapshots=(Snapshot(NOW, views),),
    )


# Channel A: all Shorts, 1,000 subs.
#   id  days  views  secs  title
CHANNEL_A = [
    video("a1", 5, 100, 20, True, "Top 5 Deadliest Snakes"),  # 22 chars, digit, top N
    video("a2", 10, 200, 45, True, "Why do CATS purr?"),  # 17, ?, caps
    video("a3", 20, 300, 59, True, "The WILD truth"),  # 14, caps
    video("a4", 30, 400, 60, True, "Top 10 fastest animals"),  # 22, digit, top N
    video("a5", 40, 5000, 120, True, "3 sharks"),  # 8, digit
    video("a6", 60, 600, 180, True, "Is this the BIGGEST bear?"),  # 25, ?, caps
    video("a7", 100, 700, 30, True, "top5 birds"),  # outside 90d; digit, top N
    video("a8", 200, 800, 150, True, "Owls at night"),  # outside 90d
]

# Channel B: 4 Shorts and 4 long-form, 10,000 subs.
CHANNEL_B = [
    video("b1", 3, 1000, 50, True, "Top 3 whales"),
    video("b2", 15, 2000, 170, True, "Whale song"),
    video("b3", 25, 10000, 300, False, "Ocean giants explained"),
    video("b4", 50, 20000, 900, False, "Deep sea: a documentary"),
    video("b5", 80, 3000, 15, True, "Krill!"),
    video("b6", 120, 40000, 1500, False, "The Blue Whale"),
    video("b7", 300, 30000, 480, False, "Squid facts"),
    video("b8", 400, 9000, 25, True, "Old short"),  # outside 365d
]


def metrics(videos, window_days: int, fmt: str, subs: int | None) -> dict:
    return channel_metrics(
        videos, window_days=window_days, fmt=fmt, now=NOW, subs=subs, config=CONFIG
    )


def test_channel_a_shorts_90d() -> None:
    m = metrics(CHANNEL_A, 90, "shorts", 1000)
    # a1..a6; views sorted 100 200 300 400 600 5000.
    assert m["video_count"] == 6
    assert m["uploads_per_week"] == pytest.approx(0.4666666666666667)  # 6 / (90 / 7)
    assert m["window_views"] == 6600
    assert m["views_median"] == 350  # (300 + 400) / 2
    assert m["views_p25"] == 225  # rank 1.25: 200 + 0.25 × 100
    assert m["views_p75"] == 550  # rank 3.75: 400 + 0.75 × 200
    assert m["views_max"] == 5000
    assert m["views_per_sub"] == 0.35  # 350 / 1000
    # Baseline: median of all 8 Shorts (100..800, 5000) = (400 + 600) / 2 = 500; bar 1500.
    assert m["outlier_baseline"] == 500
    assert m["outlier_ids"] == ["a5"]
    assert m["outlier_count"] == 1
    # 20 | 45 59 60 | 120 180
    assert m["length_buckets"] == {
        "≤30s": 1,
        "30–60s": 3,
        "60–180s": 2,
        "3–8min": 0,
        "8–20min": 0,
        "20min+": 0,
    }
    # Lengths 22 + 17 + 14 + 22 + 8 + 25 = 108 over 6 titles.
    assert m["title_features"] == {
        "mean_length": 18.0,
        "share_number": 0.5,  # a1 a4 a5
        "share_question": pytest.approx(0.3333333333333333),  # a2 a6
        "share_top_n": pytest.approx(0.3333333333333333),  # a1 a4
        "share_caps_word": 0.5,  # a2 a3 a6
    }
    assert m["velocity"] == {"d7": None, "n7": 0, "d30": None, "n30": 0}


def test_channel_a_shorts_365d() -> None:
    m = metrics(CHANNEL_A, 365, "shorts", 1000)
    # All 8; views sorted 100 200 300 400 600 700 800 5000.
    assert m["video_count"] == 8
    assert m["uploads_per_week"] == pytest.approx(0.15342465753424658)  # 8 / (365 / 7)
    assert m["views_median"] == 500
    assert m["views_p25"] == 275  # rank 1.75: 200 + 0.75 × 100
    assert m["views_p75"] == 725  # rank 5.25: 700 + 0.25 × 100
    assert m["views_max"] == 5000
    assert m["views_per_sub"] == 0.5
    assert m["outlier_ids"] == ["a5"]
    assert m["length_buckets"]["≤30s"] == 2  # 20, 30 (30 is at the top of ≤30s)
    assert m["length_buckets"]["60–180s"] == 3  # 120, 180, 150
    assert m["title_features"]["share_top_n"] == 0.375  # a1 a4 a7 of 8


def test_channel_a_has_no_longform() -> None:
    m = metrics(CHANNEL_A, 90, "longform", 1000)
    assert m["video_count"] == 0
    assert m["uploads_per_week"] == 0
    assert m["views_median"] is None
    assert m["views_p25"] is None
    assert m["views_max"] is None
    assert m["views_per_sub"] is None
    assert m["outlier_ids"] == []
    assert m["title_features"]["mean_length"] is None


def test_channel_b_formats_are_scored_separately() -> None:
    shorts = metrics(CHANNEL_B, 90, "shorts", 10000)
    # b1 b2 b5: 1000 2000 3000.
    assert shorts["video_count"] == 3
    assert shorts["views_median"] == 2000
    assert shorts["views_p25"] == 1500
    assert shorts["views_p75"] == 2500
    assert shorts["views_per_sub"] == 0.2
    # Baseline over all 4 Shorts (1000 2000 3000 9000) = 2500; bar 7500: none in window.
    assert shorts["outlier_baseline"] == 2500
    assert shorts["outlier_ids"] == []
    assert shorts["length_buckets"]["30–60s"] == 1  # 50
    assert shorts["length_buckets"]["60–180s"] == 1  # 170

    longform = metrics(CHANNEL_B, 365, "longform", 10000)
    # b3 b4 b6 b7: 10000 20000 40000 30000.
    assert longform["video_count"] == 4
    assert longform["views_median"] == 25000
    assert longform["views_p25"] == 17500
    assert longform["views_p75"] == 32500
    assert longform["views_max"] == 40000
    assert longform["outlier_ids"] == []  # bar 75000
    assert longform["length_buckets"] == {
        "≤30s": 0,
        "30–60s": 0,
        "60–180s": 0,
        "3–8min": 2,  # 300, 480
        "8–20min": 1,  # 900
        "20min+": 1,  # 1500
    }


def test_share_of_tracked_views_sums_to_one() -> None:
    by_channel = {
        "A": metrics(CHANNEL_A, 90, "shorts", 1000),
        "B": metrics(CHANNEL_B, 90, "shorts", 10000),
    }
    add_view_shares(by_channel)
    # 6600 and 6000 of 12600.
    assert by_channel["A"]["share_of_tracked_views"] == pytest.approx(0.5238095238095238)
    assert by_channel["B"]["share_of_tracked_views"] == pytest.approx(0.47619047619047616)
    total = sum(m["share_of_tracked_views"] for m in by_channel.values())
    assert total == pytest.approx(1.0)

    longform = {
        "A": metrics(CHANNEL_A, 90, "longform", 1000),
        "B": metrics(CHANNEL_B, 90, "longform", 10000),
    }
    add_view_shares(longform)
    assert longform["A"]["share_of_tracked_views"] == 0
    assert longform["B"]["share_of_tracked_views"] == 1


def test_shares_are_none_when_nobody_has_views() -> None:
    empty = {"A": metrics([], 90, "shorts", None), "B": metrics([], 90, "shorts", None)}
    add_view_shares(empty)
    assert [m["share_of_tracked_views"] for m in empty.values()] == [None, None]


def test_outlier_baseline_uses_the_newest_n_videos() -> None:
    small = MetricsConfig(3.0, 2, CONFIG.length_buckets)
    # Newest 2 Shorts are a1 (100) and a2 (200): baseline 150, bar 450.
    m = channel_metrics(CHANNEL_A, window_days=90, fmt="shorts", now=NOW, subs=1, config=small)
    assert m["outlier_baseline"] == 150
    assert m["outlier_ids"] == ["a6", "a5"]  # oldest first: a6 (600), a5 (5000)


def test_velocity_from_weekly_snapshots() -> None:
    pub = NOW - timedelta(days=60)

    def snaps(*pairs: tuple[int, int]) -> tuple[Snapshot, ...]:
        return tuple(Snapshot(pub + timedelta(days=d), v) for d, v in pairs)

    videos = [
        VideoStats("v1", "t", pub, 20, True, snaps((3, 100), (6, 150), (13, 400), (27, 900))),
        VideoStats("v2", "t", pub, 20, True, snaps((5, 50), (12, 300))),
        VideoStats("v3", "t", pub, 20, True, snaps((2, 9999))),  # one snapshot: skipped
        VideoStats("v4", "t", pub, 20, True, snaps((40, 1), (47, 2))),  # tracked too late
    ]
    # 7 days: v1 150 (day 6), v2 50 (day 5) → 100. 30 days: v1 900, v2 300 → 600.
    assert velocity(videos) == {"d7": 100, "n7": 2, "d30": 600, "n30": 2}


def test_percentile_and_top_bucket() -> None:
    assert percentile([], 0.5) is None
    assert percentile([7], 0.25) == 7
    assert top_bucket({"a": 1, "b": 3, "c": 3}) == "b"
    assert top_bucket({"a": 0}) is None


def test_monthly_views_by_publish_month() -> None:
    months = monthly_views(CHANNEL_A + CHANNEL_B, now=NOW)
    assert list(months)[0] == "2025-07"
    assert list(months)[-1] == "2026-06"
    assert len(months) == 12
    # June 2026: a1 a2 a3 b1 b2 b3 = 100 + 200 + 300 + 1000 + 2000 + 10000.
    assert months["2026-06"] == 13600
    # May: a4 (30 d → 05-31) 400, a5 (05-21) 5000, b4 (05-11) 20000, a6 (60 d → 05-01) 600.
    assert months["2026-05"] == 26000
    # b8 (400 d ago, 2025-05) is outside the 12 months.
    assert sum(months.values()) == sum(v.latest_views for v in CHANNEL_A + CHANNEL_B) - 9000


def test_metrics_config_from_scoring_yaml() -> None:
    cfg = metrics_config(load_scoring(REPO_ROOT / "config" / "scoring.yaml"))
    assert cfg == CONFIG


@pytest.mark.parametrize(
    "section",
    [
        None,
        {"outlier_multiplier": 0, "outlier_window_videos": 30, "length_buckets": []},
        {"outlier_multiplier": 3, "outlier_window_videos": 0, "length_buckets": []},
        {"outlier_multiplier": 3, "outlier_window_videos": 30, "length_buckets": []},
        {
            "outlier_multiplier": 3,
            "outlier_window_videos": 30,
            "length_buckets": [
                {"label": "a", "max_seconds": 60},
                {"label": "b", "max_seconds": 30},
            ],
        },
    ],
)
def test_metrics_config_rejects_bad_sections(section: dict | None) -> None:
    with pytest.raises(ScoringConfigError):
        metrics_config({"competitor_metrics": section} if section is not None else {})
