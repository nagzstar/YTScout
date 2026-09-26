"""050: own Analytics rows → the money model's calibration, both ways.

The own channel is not in the Partner Programme yet: Analytics has views but no revenue, so
no RPM, so the score keeps ``uncalibrated`` and says why. Once revenue arrives the same
collector fills ``own_analytics.rpm_usd`` and the flag drops by itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_analytics import END, START, VIDEO_HEADERS, table
from test_scoring import TABLE
from test_scout_score import OWN, build_example, run_score, score_rows

from ytscout.collect.analytics import collect_analytics
from ytscout.scoring import money
from ytscout.scout import score as scout_score
from ytscout.store import connect, repo
from ytscout.youtube.analytics import AnalyticsApi, FakeAnalyticsTransport

AFTER_WINDOW = datetime(2026, 9, 24, tzinfo=UTC)
TABLE_MID = TABLE.row(*money.REFERENCE_ROW).mid
VIDEOS = {"s1": 5_000, "s2": 3_000, "s3": 1_200, "s4": 400}  # s4 under 1,000 views


class OwnHandler:
    """Per-video rows for the own channel; ``revenue`` is ``None`` (not monetised, the
    metric comes back empty), ``0.0`` (monetary scope, nothing earned) or a USD figure
    per 1,000 views."""

    def __init__(self, revenue_per_k: float | None) -> None:
        self.revenue_per_k = revenue_per_k

    def __call__(self, params: dict[str, Any]) -> dict:
        if params["dimensions"] == "video":
            ids = params["filters"].removeprefix("video==").split(",")
            rows = []
            for v in ids:
                views = VIDEOS[v]
                rev = None if self.revenue_per_k is None else self.revenue_per_k * views / 1000
                rows.append([v, views, rev, 100.0, 20.0, 80.0, 5, 1, 0])
            return table(VIDEO_HEADERS, rows)
        if params["dimensions"] == "day":
            return table(["day", "views", "estimatedRevenue", "monetizedPlaybacks"], [])
        return table(["insightTrafficSourceType", "views"], [])


def collect_own(revenue_per_k: float | None):
    conn = connect(Path(":memory:"))
    with conn:
        repo.upsert_channel(conn, OWN, role="own")
        for vid in VIDEOS:
            repo.upsert_video(conn, vid, channel_id=OWN, published_at=AFTER_WINDOW, duration_s=30)
    api = AnalyticsApi(FakeAnalyticsTransport(OwnHandler(revenue_per_k)))
    collect_analytics(api, conn, list(VIDEOS), start=START, end=END)
    return conn


@pytest.mark.parametrize("revenue_per_k", [None, 0.0])
def test_views_without_revenue_leave_the_model_uncalibrated(revenue_per_k: float | None) -> None:
    conn = collect_own(revenue_per_k)
    views = conn.execute(
        "SELECT COUNT(*) FROM own_analytics WHERE views >= 1000 AND window_end = ?", (END,)
    ).fetchone()[0]
    assert views == 3  # the rows are there and qualify on views...
    own = scout_score.own_rpm_usd(conn, now=AFTER_WINDOW, shorts_max_seconds=180)
    assert own is None or own == 0  # ...but carry no usable RPM
    assert (
        scout_score.calibration_from_db(conn, TABLE, now=AFTER_WINDOW, shorts_max_seconds=180)
        is None
    )


def test_monetised_rows_fill_rpm_and_calibrate() -> None:
    conn = collect_own(0.12)
    rpms = [r[0] for r in conn.execute("SELECT rpm_usd FROM own_analytics ORDER BY video_id")]
    assert rpms == pytest.approx([0.12] * 4)
    own = scout_score.own_rpm_usd(conn, now=AFTER_WINDOW, shorts_max_seconds=180)
    assert own == pytest.approx(0.12)
    cal = scout_score.calibration_from_db(conn, TABLE, now=AFTER_WINDOW, shorts_max_seconds=180)
    assert cal == pytest.approx(0.12 / TABLE_MID)


# ------------------------------------------------------------- score keeps the flag, says why


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def test_views_but_no_rpm_keeps_uncalibrated_and_explains(conn) -> None:
    build_example(conn, own=False)
    with conn:
        repo.upsert_channel(conn, OWN, role="own")
        for vid, views in (("own1", 5_000), ("own2", 2_000)):
            repo.upsert_video(conn, vid, channel_id=OWN, published_at=AFTER_WINDOW, duration_s=30)
            repo.upsert_own_analytics(
                conn, vid, "2025-07-28", "2026-08-31", views=views, rpm_usd=None
            )
    result = run_score(conn)
    (row,) = score_rows(conn)
    assert "uncalibrated" in json.loads(row["confidence_flags_json"])
    table_text = scout_score.format_table(result)
    assert "note: uncalibrated: no monetised views yet" in table_text


def test_calibrated_score_has_no_note(conn) -> None:
    build_example(conn)  # own Analytics row with an RPM
    result = run_score(conn)
    (row,) = score_rows(conn)
    assert "uncalibrated" not in json.loads(row["confidence_flags_json"])
    assert "no monetised views yet" not in scout_score.format_table(result)


def test_flag_label() -> None:
    assert money.flag_label("uncalibrated") == "uncalibrated: no monetised views yet"
    assert money.flag_label("low_confidence") == "low_confidence"
