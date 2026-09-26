"""044: summary candidates are each channel's outliers then newest, channels round-robin;
the competitor packet shows each channel's highest-view summarised videos."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ytscout import packets
from ytscout.scoring import ScoringConfigError, load_scoring, summaries_per_channel
from ytscout.settings import find_repo_root
from ytscout.store import connect, repo

SRC_ROOT = find_repo_root(Path(__file__).parent)
DAILY, WEEKLY, SLOW = "UCdaily", "UCweekly", "UCslow"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def _video(conn: sqlite3.Connection, vid: str, channel: str, day: int, views: int) -> None:
    """A Short published on 2026-``day`` (day of year as a date), with a transcript."""
    month, dom = divmod(day, 28)
    repo.upsert_video(
        conn,
        vid,
        channel_id=channel,
        title=vid,
        published_at=f"2026-{month + 1:02d}-{dom + 1:02d}T00:00:00Z",
        duration_s=40,
        is_short=True,
    )
    repo.add_video_snapshot(conn, vid, views=views, likes=0, comments=0)
    repo.put_transcript(conn, vid, language="en", text="t", source="auto", status="ok")


def seed_three(conn: sqlite3.Connection) -> dict[str, str]:
    """Three approved channels; returns each channel's biggest video id.

    DAILY posts every day (60 videos, ~1k views) with one 50k hit two months back.
    WEEKLY posts weekly (8 videos, ~2k) with a 40k hit at the oldest end.
    SLOW has 5 videos, its 9k hit in the middle.
    """
    with conn:
        for cid in (DAILY, WEEKLY, SLOW):
            repo.upsert_channel(conn, cid, role="competitor", title=cid)
            repo.set_channel_status(conn, cid, "approved")
        for i in range(60):
            _video(conn, f"d{i:02d}", DAILY, 100 + i, 50_000 if i == 0 else 1000 + i)
        for i in range(8):
            _video(conn, f"w{i}", WEEKLY, 100 + 7 * i, 40_000 if i == 0 else 2000 + i)
        for i, views in enumerate([800, 900, 9000, 700, 1000]):
            _video(conn, f"s{i}", SLOW, 100 + 14 * i, views)
    return {DAILY: "d00", WEEKLY: "w0", SLOW: "s2"}


def test_a_daily_poster_does_not_crowd_out_the_rest(conn: sqlite3.Connection) -> None:
    biggest = seed_three(conn)
    plan = repo.summary_plan(conn, "h", 12, per_channel=8)
    assert len(plan) == 12
    by_channel: dict[str, list[str]] = {}
    for c in plan:
        by_channel.setdefault(c.channel_id, []).append(c.video_id)
    for cid in (DAILY, WEEKLY, SLOW):
        assert len(by_channel[cid]) >= 3, (cid, by_channel)
        assert biggest[cid] in by_channel[cid]
        # The outlier leads the channel's queue.
        assert by_channel[cid][0] == biggest[cid]
    outliers = {c.video_id for c in plan if c.outlier}
    assert outliers == set(biggest.values())
    # Round-robin: the first three picks are one per channel.
    assert {c.channel_id for c in plan[:3]} == {DAILY, WEEKLY, SLOW}
    # After the outlier, the daily channel's newest follow.
    assert by_channel[DAILY][1:] == ["d59", "d58", "d57"]


def test_per_channel_caps_and_own_channel_comes_first(conn: sqlite3.Connection) -> None:
    seed_three(conn)
    with conn:
        repo.upsert_channel(conn, "UCown", role="own", title="Countdown Animal Kingdom")
        _video(conn, "o1", "UCown", 50, 300)
        _video(conn, "o2", "UCown", 60, 200)
    plan = repo.summary_plan(conn, "h", 40, per_channel=2)
    assert [c.video_id for c in plan[:2]] == ["o2", "o1"]
    assert len(plan) == 8  # 2 own + 2 x 3 competitors
    ids = repo.summary_candidates(conn, "h", 40, per_channel=2)
    assert ids == [c.video_id for c in plan]


def test_titles_only_summary_is_redone_once_a_transcript_arrives(
    conn: sqlite3.Connection,
) -> None:
    seed_three(conn)
    with conn:
        repo.upsert_video(
            conn,
            "s9",
            channel_id=SLOW,
            title="s9",
            is_short=True,
            published_at="2026-01-01T00:00:00Z",
        )
        repo.add_video_snapshot(conn, "s9", views=90_000, likes=0, comments=0)
        repo.put_transcript(conn, "s9", language="", text=None, source=None, status="error")
        repo.put_video_summary(
            conn, "s9", prompt_hash="h", schema_hash="x", transcript_status="error", summary={}
        )
    assert "s9" not in repo.summary_candidates(conn, "h", 100)
    with conn:
        repo.put_transcript(conn, "s9", language="en", text="now", source="auto", status="ok")
    assert repo.summary_candidates(conn, "h", 100, per_channel=1) == ["d00", "s9", "w0"]


def test_packet_videos_are_the_channels_highest_view_summaries(
    conn: sqlite3.Connection,
) -> None:
    seed_three(conn)
    with conn:
        # Summarise the newest five of DAILY plus its old 50k hit and a mid one.
        for vid in ("d59", "d58", "d57", "d56", "d55", "d00", "d30"):
            repo.put_video_summary(
                conn, vid, prompt_hash="h", schema_hash="x", transcript_status="ok", summary={}
            )
    rows = repo.summarised_videos_for_channel(conn, DAILY, 3, "shorts")
    assert [r["id"] for r in rows] == ["d00", "d59", "d58"]
    assert [r["views"] for r in rows] == [50_000, 1059, 1058]
    packet = packets.competitor_packet(conn)
    daily = next(c for c in packet["channels"] if c["id"] == DAILY)
    views = [v["views"] for v in daily["videos"]]
    assert daily["videos"][0]["video_id"] == "d00"
    assert views == sorted(views, reverse=True)


def test_per_channel_config() -> None:
    assert summaries_per_channel(load_scoring(SRC_ROOT / "config" / "scoring.yaml")) == 8
    for bad in ({}, {"video_summaries": {"per_channel": 0}}, {"video_summaries": None}):
        with pytest.raises(ScoringConfigError, match="per_channel"):
            summaries_per_channel(bad)
