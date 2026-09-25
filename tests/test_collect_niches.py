"""``collect --niches``: the weekly refresh of tracked niches, against ``FakeTransport``."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from test_collect_competitors import PARTS, channel_item, playlist_key, video_item
from test_scout_score import NOW, add_niche, repo_root  # noqa: F401  (repo_root: a fixture)

from ytscout import cli
from ytscout.cli import EXIT_OK, EXIT_QUOTA_EXHAUSTED, main
from ytscout.collect.niches import collect_niches, tracked_niches
from ytscout.store import connect, repo
from ytscout.youtube import DataApi, FakeTransport, Ledger

key = FakeTransport.key
TRACKED = ("UCa", "UCb", "UCc")


def page(*entries: tuple[str, int], next_token: str | None = None) -> dict:
    p: dict = {
        "items": [
            {"contentDetails": {"videoId": vid, "videoPublishedAt": ago_now(days)}}
            for vid, days in entries
        ]
    }
    if next_token:
        p["nextPageToken"] = next_token
    return p


def ago_now(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def vid_item(vid: str, cid: str, days: int, views: int) -> dict:
    item = video_item(vid, cid, 0, views)
    item["snippet"]["publishedAt"] = ago_now(days)
    return item


def fixtures() -> dict:
    """UCa: a2 is known and 200 days old (skipped), a page 2 that must never be read.
    UCb: one new old video (fetched: new). UCc: nothing on its page."""
    return {
        key("channels", "list", part=PARTS, id="UCa,UCb,UCc", maxResults=50): {
            "items": [channel_item(c, f"Niche {c}", 5000) for c in TRACKED]
        },
        playlist_key("UCa"): [page(("a1", 3), ("a2", 200), next_token="p2"), page(("a3", 400))],
        playlist_key("UCb"): [page(("b1", 365))],
        playlist_key("UCc"): [page()],
        key("videos", "list", part=PARTS, id="a1,b1", maxResults=50): {
            "items": [vid_item("a1", "UCa", 3, 1000), vid_item("b1", "UCb", 365, 90)]
        },
    }


def seed(conn: sqlite3.Connection) -> tuple[int, int]:
    """One tracked niche of three channels and one shelved niche of one; returns their ids."""
    with conn:
        tracked = add_niche(conn, topic="tracked-topic", status="track")
        shelved = add_niche(conn, topic="shelved-topic", status="shelve")
        for cid in (*TRACKED, "UCs"):
            repo.upsert_channel(conn, cid, role="niche_sample", title=cid)
        for cid in TRACKED:
            repo.put_niche_channel(conn, tracked, cid, is_small=True)
        repo.put_niche_channel(conn, shelved, "UCs", is_small=True)
        repo.upsert_video(
            conn, "a2", channel_id="UCa", published_at=ago_now(200), duration_s=40, is_short=True
        )
        repo.add_video_snapshot(conn, "a2", views=5, likes=0, comments=0, captured_at=ago_now(1))
    return tracked, shelved


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "db.sqlite")
    yield c
    c.close()


def calls(transport: FakeTransport) -> list[tuple[str, str]]:
    return [(r, p.get("id") or p.get("playlistId")) for r, _m, p in transport.calls]


def count(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def test_tracked_niche_refresh_calls_and_units(conn) -> None:
    tracked, shelved = seed(conn)
    transport = FakeTransport(fixtures())
    ledger = Ledger(conn, 9000, run_cap=100)
    result = collect_niches(
        DataApi(ledger, transport), conn, tracked_niches(conn), shorts_max_seconds=180, now=NOW
    )
    assert result.stopped is None and result.finished == [tracked]
    assert calls(transport) == [
        ("channels", "UCa,UCb,UCc"),
        ("playlistItems", "UUa"),
        ("playlistItems", "UUb"),
        ("playlistItems", "UUc"),
        ("videos", "a1,b1"),
    ]
    assert all(r != "search" for r, _ in calls(transport))
    # 1 channels + 3 first pages + 1 pooled videos call.
    assert ledger.run_used == 5
    (report,) = result.reports
    assert (report.channels, report.pages, report.video_batches, report.units) == (3, 3, 1, 5)
    for cid in TRACKED:
        assert count(conn, "SELECT COUNT(*) FROM channel_snapshots WHERE channel_id = ?", cid) == 1
    assert repo.get_video(conn, "a1")["channel_id"] == "UCa"
    assert count(conn, "SELECT COUNT(*) FROM video_snapshots WHERE video_id = 'a2'") == 1
    assert repo.get_video(conn, "a3") is None  # page 2 is never read


def test_shelved_niche_is_untouched(conn) -> None:
    seed(conn)
    transport = FakeTransport(fixtures())
    collect_niches(
        DataApi(Ledger(conn, 9000, run_cap=100), transport),
        conn,
        tracked_niches(conn),
        shorts_max_seconds=180,
        now=NOW,
    )
    assert not any("UCs" in str(p) or "UUs" in str(p) for _r, _m, p in transport.calls)
    assert count(conn, "SELECT COUNT(*) FROM channel_snapshots WHERE channel_id = 'UCs'") == 0


def test_a_channel_shared_by_two_tracked_niches_is_refreshed_once(conn) -> None:
    tracked, _ = seed(conn)
    with conn:
        second = add_niche(conn, topic="second", status="tracking")
        repo.put_niche_channel(conn, second, "UCa", is_small=True)
    transport = FakeTransport(fixtures())
    result = collect_niches(
        DataApi(Ledger(conn, 9000, run_cap=100), transport),
        conn,
        tracked_niches(conn),
        shorts_max_seconds=180,
        now=NOW,
    )
    assert result.finished == [tracked, second]
    assert result.reports[1].shared == 1 and result.reports[1].units == 0
    assert len(transport.calls) == 5


def test_quota_stop_keeps_finished_batches(conn) -> None:
    seed(conn)
    ledger = Ledger(conn, 9000, run_cap=2)
    result = collect_niches(
        DataApi(ledger, FakeTransport(fixtures())),
        conn,
        tracked_niches(conn),
        shorts_max_seconds=180,
        now=NOW,
    )
    assert result.stopped is not None and result.finished == []
    assert count(conn, "SELECT COUNT(*) FROM channel_snapshots") == 3  # committed


# --- CLI ---------------------------------------------------------------------------------


def db_path(root: Path) -> Path:
    return root / "data" / "ytscout.sqlite"


def test_cli_collect_niches_appends_a_niche_scores_row(
    repo_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    c = connect(db_path(repo_root))
    tracked, shelved = seed(c)
    c.close()
    monkeypatch.setattr(cli, "make_transport", lambda s, d: FakeTransport(fixtures()))
    assert main(["collect", "--niches", "--max-units", "100"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"niche {tracked} Top 5 countdown: tracked-topic: 3 channel(s)" in out
    assert "- 5 unit(s)" in out
    assert "1 of 1 niche(s) refreshed" in out and "units this run 5" in out
    assert "1 niche_scores row(s) appended" in out
    c = sqlite3.connect(db_path(repo_root))
    try:
        assert c.execute("SELECT niche_id FROM niche_scores").fetchall() == [(tracked,)]
        kinds = [r[0] for r in c.execute("SELECT kind FROM runs ORDER BY id")]
        assert kinds == ["collect_niches", "score_niches"]
        # The track decision survives the re-score.
        assert c.execute("SELECT status FROM niches WHERE id = ?", (tracked,)).fetchone() == (
            "track",
        )
    finally:
        c.close()


def test_cli_quota_stop_exits_3_without_scoring(
    repo_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = connect(db_path(repo_root))
    seed(c)
    c.close()
    monkeypatch.setattr(cli, "make_transport", lambda s, d: FakeTransport(fixtures()))
    assert main(["collect", "--niches", "--max-units", "2"]) == EXIT_QUOTA_EXHAUSTED
    c = sqlite3.connect(db_path(repo_root))
    try:
        assert c.execute("SELECT status FROM runs").fetchone() == ("quota_exhausted",)
        assert c.execute("SELECT COUNT(*) FROM niche_scores").fetchone() == (0,)
    finally:
        c.close()


def test_cli_dry_run_estimates_a_30_channel_niche_under_60_units(
    repo_root: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    c = connect(db_path(repo_root))
    with c:
        niche = add_niche(c, topic="big", status="track")
        add_niche(c, topic="shelved", status="shelve")
        for i in range(30):
            repo.upsert_channel(c, f"UC{i:02d}", role="niche_sample")
            repo.put_niche_channel(c, niche, f"UC{i:02d}", is_small=False)
    c.close()
    before = db_path(repo_root).read_bytes()
    assert main(["collect", "--niches", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    # 1 channels call + 30 first pages + 1 pooled videos call.
    assert "30 channel(s); channels.list x 1, playlistItems.list x 30, videos.list x 1" in out
    assert "- 32 unit(s)" in out
    assert "planned: 32 units for 1 tracked niche(s); no search.list" in out
    assert db_path(repo_root).read_bytes() == before
