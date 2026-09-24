"""``collect --competitors`` and ``score --competitors`` against ``FakeTransport``."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_dashboard import assert_self_contained

from ytscout import cli
from ytscout.cli import EXIT_ERROR, EXIT_OK, EXIT_QUOTA_EXHAUSTED, main
from ytscout.collect.competitors import STOP_END, STOP_KNOWN, collect_competitors
from ytscout.dashboard import build
from ytscout.store import connect, read_copy, repo, to_utc_iso
from ytscout.youtube import DataApi, FakeTransport, Ledger
from ytscout.youtube.client import PAGE_SIZE

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 30, tzinfo=UTC)
PARTS = "snippet,statistics,contentDetails"
OWN = "UCown"
key = FakeTransport.key


def ago(days: int) -> str:
    return to_utc_iso(NOW - timedelta(days=days))


def channel_item(cid: str, title: str, subs: int) -> dict:
    return {
        "id": cid,
        "snippet": {"title": title},
        "statistics": {"subscriberCount": str(subs), "viewCount": "1", "videoCount": "1"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
    }


def playlist_page(*entries: tuple[str, int]) -> dict:
    return {
        "items": [
            {"contentDetails": {"videoId": vid, "videoPublishedAt": ago(days)}}
            for vid, days in entries
        ]
    }


def video_item(vid: str, cid: str, days: int, views: int) -> dict:
    return {
        "id": vid,
        "snippet": {"channelId": cid, "title": f"Top 5 {vid}", "publishedAt": ago(days)},
        "contentDetails": {"duration": "PT45S"},
        "statistics": {"viewCount": str(views), "likeCount": "1", "commentCount": "0"},
    }


def playlist_key(cid: str):
    return key(
        "playlistItems",
        "list",
        part="snippet,contentDetails",
        playlistId="UU" + cid[2:],
        maxResults=PAGE_SIZE,
    )


def fixtures() -> dict:
    """Own (1 new video); UCcomp: 3 pages, stops after page 2; UCwatch: 1 page."""
    return {
        key("channels", "list", part=PARTS, id="UCown,UCcomp,UCwatch", maxResults=50): {
            "items": [
                channel_item(OWN, "Countdown", 500),
                channel_item("UCcomp", "Approved Beasts", 2000),
                channel_item("UCwatch", "Watched Wildlife", 800),
            ]
        },
        playlist_key(OWN): [playlist_page(("o1", 1))],
        playlist_key("UCcomp"): [
            playlist_page(("c1", 5), ("c2", 10)),
            # c3 is known but recent: re-fetched. c4 is known and 200 days old: the stop.
            playlist_page(("c3", 30), ("c4", 200)),
            playlist_page(("c5", 300)),
        ],
        # w1 known but recent, w2 new though old: both fetched; the playlist just ends.
        playlist_key("UCwatch"): [playlist_page(("w1", 20), ("w2", 400))],
        key("videos", "list", part=PARTS, id="o1", maxResults=50): {
            "items": [video_item("o1", OWN, 1, 50)]
        },
        key("videos", "list", part=PARTS, id="c1,c2,c3", maxResults=50): {
            "items": [
                video_item("c1", "UCcomp", 5, 100),
                video_item("c2", "UCcomp", 10, 200),
                video_item("c3", "UCcomp", 30, 3000),
            ]
        },
        key("videos", "list", part=PARTS, id="w1,w2", maxResults=50): {
            "items": [video_item("w1", "UCwatch", 20, 70), video_item("w2", "UCwatch", 400, 90)]
        },
    }


def seed(conn: sqlite3.Connection) -> None:
    with conn:
        repo.upsert_channel(conn, OWN, role="own", title="Countdown")
        for cid, status in (
            ("UCcomp", "approved"),
            ("UCwatch", "watch"),
            ("UCrej", "rejected"),
            ("UCcand", None),
        ):
            repo.upsert_channel(conn, cid, role="competitor", title=cid)
            repo.set_channel_status(conn, cid, status)
        for vid, cid, days, views in (
            ("c3", "UCcomp", 30, 1000),
            ("c4", "UCcomp", 200, 4000),
            ("w1", "UCwatch", 20, 60),
        ):
            repo.upsert_video(
                conn, vid, channel_id=cid, published_at=ago(days), duration_s=45, is_short=True
            )
            repo.add_video_snapshot(
                conn, vid, views=views, likes=0, comments=0, captured_at=ago(days - 1)
            )


def snapshot_count(conn: sqlite3.Connection, vid: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM video_snapshots WHERE video_id = ?", (vid,)
    ).fetchone()[0]


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "db.sqlite")
    seed(c)
    yield c
    c.close()


def test_walk_stops_at_the_first_page_whose_oldest_is_known_and_old(conn) -> None:
    transport = FakeTransport(fixtures())
    ledger = Ledger(conn, 9000, run_cap=50)
    result = collect_competitors(
        DataApi(ledger, transport),
        conn,
        [dict(r) for r in repo.tracked_channels(conn)],
        own_channel_id=OWN,
        shorts_max_seconds=180,
        now=NOW,
    )
    assert result.stopped is None
    pages = [c[2].get("pageToken") for c in transport.calls if c[2].get("playlistId") == "UUcomp"]
    assert pages == [None, "page1"]  # page 3 (c5) never requested

    reports = {r.channel_id: r for r in result.reports}
    assert list(reports) == [OWN, "UCcomp", "UCwatch"]
    assert (reports["UCcomp"].pages, reports["UCcomp"].stop) == (2, STOP_KNOWN)
    assert (reports["UCwatch"].pages, reports["UCwatch"].stop) == (1, STOP_END)
    # 1 channels call; own 1 page + 1 batch; comp 2 pages + 1 batch; watch 1 page + 1 batch.
    assert ledger.run_used == 8
    assert (reports[OWN].units, reports["UCcomp"].units, reports["UCwatch"].units) == (2, 3, 2)

    assert repo.get_video(conn, "c5") is None
    assert snapshot_count(conn, "c3") == 2  # recent: re-snapshotted
    assert snapshot_count(conn, "c4") == 1  # old: keeps its last snapshot
    assert snapshot_count(conn, "w1") == 2
    assert snapshot_count(conn, "w2") == 1  # new, however old
    assert repo.latest_video_snapshot(conn, "c3")["views"] == 3000
    # Rejected and candidate channels are not tracked.
    assert repo.latest_channel_snapshot(conn, "UCrej") is None
    assert repo.latest_channel_snapshot(conn, "UCcomp")["subs"] == 2000
    assert repo.get_channel(conn, "UCcomp")["uploads_playlist_id"] == "UUcomp"
    assert repo.get_channel(conn, OWN)["role"] == "own"


def test_second_week_walks_the_same_pages(conn) -> None:
    week2 = fixtures()
    # Week 2: w2 is now known and 400 days old, so only w1 is re-fetched.
    week2[key("videos", "list", part=PARTS, id="w1", maxResults=50)] = {
        "items": [video_item("w1", "UCwatch", 20, 80)]
    }
    for _ in range(2):
        transport = FakeTransport(week2)
        ledger = Ledger(conn, 9000, run_cap=50)
        result = collect_competitors(
            DataApi(ledger, transport),
            conn,
            [dict(r) for r in repo.tracked_channels(conn)],
            own_channel_id=OWN,
            shorts_max_seconds=180,
            now=NOW,
        )
    # Week 2: UCcomp still needs page 2 (page 1's oldest, c2, is only 10 days old).
    assert result.stopped is None
    assert {r.channel_id: r.pages for r in result.reports} == {OWN: 1, "UCcomp": 2, "UCwatch": 1}
    fetched = [c[2]["id"] for c in transport.calls if c[0] == "videos"]
    assert fetched == ["o1", "c1,c2,c3", "w1"]
    assert snapshot_count(conn, "w2") == 1


def test_quota_stop_keeps_finished_batches(conn) -> None:
    ledger = Ledger(conn, 9000, run_cap=4)
    result = collect_competitors(
        DataApi(ledger, FakeTransport(fixtures())),
        conn,
        [dict(r) for r in repo.tracked_channels(conn)],
        own_channel_id=OWN,
        shorts_max_seconds=180,
        now=NOW,
    )
    assert result.stopped is not None and result.stopped.kind == "run"
    assert repo.get_video(conn, "o1") is not None  # own batch committed before the stop
    assert repo.get_video(conn, "c1") is None


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(f"own_channel_id: {OWN}\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "config" / "scoring.yaml", config / "scoring.yaml")
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)
    c = connect(tmp_path / "data" / "ytscout.sqlite")
    seed(c)
    c.close()
    return tmp_path


def db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "data" / "ytscout.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def test_cli_collect_then_score(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport(fixtures())
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: transport)
    assert main(["collect", "--competitors", "--max-units", "20"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "collect --competitors: channels 3, videos 6" in out

    assert main(["score", "--competitors"]) == EXIT_OK
    assert "12 channel_metrics rows for 3 channel(s)" in capsys.readouterr().out
    conn = db(repo_root)
    rows = conn.execute("SELECT channel_id, window, format FROM channel_metrics").fetchall()
    assert len(rows) == 12
    assert {(r[0], r[1], r[2]) for r in rows} == {
        (c, w, f)
        for c in (OWN, "UCcomp", "UCwatch")
        for w in ("90d", "365d")
        for f in ("shorts", "longform")
    }
    # Appended, never replaced: a second score doubles the rows; latest reads 12.
    assert main(["score", "--competitors"]) == EXIT_OK
    assert conn.execute("SELECT COUNT(*) FROM channel_metrics").fetchone()[0] == 24
    assert len(repo.latest_channel_metrics(conn)) == 12
    kinds = [r[0] for r in conn.execute("SELECT kind FROM runs ORDER BY id")]
    assert kinds == ["collect_competitors", "score_competitors", "score_competitors"]


def test_cli_dry_run_plans_per_channel_and_writes_nothing(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (repo_root / "data" / "ytscout.sqlite").read_bytes()
    assert main(["collect", "--competitors", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "channels.list x 1" in out
    for cid in (OWN, "UCcomp", "UCwatch"):
        assert f"{cid} ({cid if cid != OWN else 'Countdown'})" in out
    assert "UCrej" not in out and "UCcand" not in out
    # 1 channels + 3 × (1 page + 1 batch).
    assert "planned: 7 calls, 7 units for 3 tracked channel(s)" in out
    assert (repo_root / "data" / "ytscout.sqlite").read_bytes() == before


def test_cli_requires_one_source_and_a_quota_flag(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--competitors"]) == EXIT_ERROR
    assert "--max-units" in capsys.readouterr().err
    assert main(["collect", "--own", "--competitors", "--dry-run"]) == EXIT_ERROR
    assert main(["score"]) == EXIT_ERROR
    assert "--competitors" in capsys.readouterr().err


def test_cli_quota_stop_exits_3(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "make_transport", lambda s, d: FakeTransport(fixtures()))
    assert main(["collect", "--competitors", "--max-units", "4"]) == EXIT_QUOTA_EXHAUSTED
    status = db(repo_root).execute("SELECT status FROM runs").fetchone()[0]
    assert status == "quota_exhausted"


def test_dashboard_shows_side_by_side_metrics_and_monthly_chart(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "make_transport", lambda s, d: FakeTransport(fixtures()))
    assert main(["collect", "--competitors", "--max-units", "20"]) == EXIT_OK
    assert main(["score", "--competitors"]) == EXIT_OK
    conn = read_copy(repo_root / "data" / "ytscout.sqlite")
    try:
        dash = build(conn, tmp_path / "index.html", now=NOW)
    finally:
        conn.close()
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert_self_contained(html)

    rows = dash.metrics["90d"]["shorts"]
    assert [r["id"] for r in rows] == [OWN, "UCcomp", "UCwatch"]  # own first
    assert rows[0]["own"] and not rows[1]["own"]
    assert '<tr data-channel-id="UCown" class="own">' in html
    assert 'data-window-panel="90d"' in html and 'data-window-panel="365d" hidden' in html
    # UCcomp 90d Shorts: c1 100, c2 200, c3 3000 → median 200; all 45 s → "30–60s".
    comp = rows[1]
    assert comp["views_median"] == 200 and comp["top_bucket"] == "30–60s"

    monthly = dash.monthly
    assert monthly["labels"][-1] == "2026-06" and len(monthly["labels"]) == 12
    by_name = {s["name"]: s["data"] for s in monthly["series"]}
    # June 2026: c1 (06-25) 100 + c2 (06-20) 200; May: c3 (05-31) 3000.
    assert by_name["Approved Beasts"][-2:] == [3000, 300]
    assert 'id="monthly-data"' in html and "views by publish month" in html.lower()


def test_monthly_chart_folds_past_eight_series(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    with conn:
        repo.upsert_channel(conn, OWN, role="own", title="Own")
        for i in range(10):
            cid = f"UC{i:02d}"
            repo.upsert_channel(conn, cid, role="competitor", title=f"C{i:02d}")
            repo.set_channel_status(conn, cid, "approved")
            repo.upsert_video(conn, f"v{i}", channel_id=cid, published_at=ago(5), is_short=True)
            repo.add_video_snapshot(conn, f"v{i}", views=100 * (i + 1), likes=0, comments=0)
    dash = build(conn, tmp_path / "index.html", now=NOW)
    conn.close()
    names = [s["name"] for s in dash.monthly["series"]]
    # Own + the 6 biggest competitors + Other (the 4 smallest) = 8 lines.
    assert names == ["Own", "C09", "C08", "C07", "C06", "C05", "C04", "Other (4 channels)"]
    assert dash.monthly["series"][-1]["data"][-1] == 100 + 200 + 300 + 400
