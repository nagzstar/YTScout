"""``collect --own`` end to end, in-process, against ``FakeTransport`` and a tmp DB."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from ytscout import cli
from ytscout.cli import EXIT_ERROR, EXIT_OK, EXIT_QUOTA_EXHAUSTED, main
from ytscout.youtube import DryRunTransport, FakeTransport
from ytscout.youtube.client import PAGE_SIZE

FIXTURES = Path(__file__).parent / "fixtures" / "own_channel"
REPO_ROOT = Path(__file__).resolve().parents[1]
CHANNEL_ID = "UCownchannel00000000001"
UPLOADS = "UUownchannel00000000001"
VIDEO_IDS = [f"own0000000{i}" for i in range(1, 6)]
PARTS = "snippet,statistics,contentDetails"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def own_fixtures() -> dict:
    key = FakeTransport.key
    return {
        key("channels", "list", part=PARTS, id=CHANNEL_ID, maxResults=50): _load("channel"),
        key(
            "playlistItems",
            "list",
            part="snippet,contentDetails",
            playlistId=UPLOADS,
            maxResults=PAGE_SIZE,
        ): _load("playlist_items"),
        key("videos", "list", part=PARTS, id=",".join(VIDEO_IDS), maxResults=50): _load("videos"),
    }


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repo root: settings, the real scoring.yaml, no .env, cwd set to it."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(f"own_channel_id: {CHANNEL_ID}\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "config" / "scoring.yaml", config / "scoring.yaml")
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    transport = FakeTransport(own_fixtures())
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: transport)
    return transport


def db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "data" / "ytscout.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_collect_own_writes_channel_videos_and_snapshots(
    repo_root: Path, fake: FakeTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--own", "--max-units", "10"]) == EXIT_OK
    conn = db(repo_root)
    assert count(conn, "channels") == 1
    assert count(conn, "channel_snapshots") == 1
    assert count(conn, "videos") == 5
    assert count(conn, "video_snapshots") == 5

    channel = conn.execute("SELECT * FROM channels").fetchone()
    assert channel["id"] == CHANNEL_ID
    assert channel["role"] == "own"
    assert channel["uploads_playlist_id"] == UPLOADS
    snap = conn.execute("SELECT * FROM channel_snapshots").fetchone()
    assert (snap["subs"], snap["view_count"], snap["video_count"]) == (1200, 250000, 5)

    short = conn.execute("SELECT * FROM videos WHERE id = 'own00000001'").fetchone()
    long = conn.execute("SELECT * FROM videos WHERE id = 'own00000002'").fetchone()
    assert (short["duration_s"], short["is_short"]) == (45, 1)
    assert (long["duration_s"], long["is_short"]) == (200, 0)
    exactly_180 = conn.execute("SELECT is_short FROM videos WHERE id = 'own00000004'").fetchone()
    assert exactly_180[0] == 1  # the cut-off is inclusive
    untagged = conn.execute("SELECT tags_json FROM videos WHERE id = 'own00000003'").fetchone()
    assert json.loads(untagged[0]) == []
    assert json.loads(short["tags_json"]) == ["animals", "top5"]
    assert short["category_id"] == "15"
    assert short["published_at"] == "2026-09-20T10:00:00Z"

    vsnap = conn.execute(
        "SELECT views, likes, comments FROM video_snapshots s JOIN videos v ON v.id = s.video_id"
        " WHERE v.id = 'own00000002'"
    ).fetchone()
    assert tuple(vsnap) == (2000, 80, 9)

    # 1 channels + 1 playlistItems + 1 videos
    assert [(r, m) for r, m, _ in fake.calls] == [
        ("channels", "list"),
        ("playlistItems", "list"),
        ("videos", "list"),
    ]
    assert conn.execute("SELECT SUM(units_used) FROM quota_ledger").fetchone()[0] == 3

    run = conn.execute("SELECT * FROM runs").fetchone()
    assert (run["kind"], run["status"]) == ("collect_own", "ok")
    assert run["started_at"] and run["finished_at"]

    out = capsys.readouterr().out
    assert "channels 1, videos 5, snapshots 6 (1 channel, 5 video)" in out
    assert "units this run 3, units today 3" in out


def test_second_run_appends_snapshots_and_upserts_videos(
    repo_root: Path, fake: FakeTransport
) -> None:
    assert main(["collect", "--own", "--max-units", "10"]) == EXIT_OK
    assert main(["collect", "--own", "--max-units", "10"]) == EXIT_OK
    conn = db(repo_root)
    assert count(conn, "channels") == 1
    assert count(conn, "videos") == 5
    assert count(conn, "video_snapshots") == 10
    assert count(conn, "channel_snapshots") == 2
    assert count(conn, "runs") == 2


def test_videos_limit_stops_early(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures = own_fixtures()
    three = _load("videos")
    three["items"] = three["items"][:3]
    fixtures[
        FakeTransport.key("videos", "list", part=PARTS, id=",".join(VIDEO_IDS[:3]), maxResults=50)
    ] = three
    transport = FakeTransport(fixtures)
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: transport)
    assert main(["collect", "--own", "--videos", "3", "--max-units", "10"]) == EXIT_OK
    assert count(db(repo_root), "videos") == 3


def test_dry_run_writes_nothing(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["collect", "--own", "--dry-run"]) == EXIT_OK
    assert not (repo_root / "data").exists()
    out = capsys.readouterr().out
    assert "channels.list" in out and "playlistItems.list" in out and "videos.list" in out
    assert out.count("1 unit(s)") == 3
    assert "planned: 3 calls, 3 units" in out


def test_dry_run_leaves_an_existing_db_untouched(
    repo_root: Path, fake: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["collect", "--own", "--max-units", "10"]) == EXIT_OK
    path = repo_root / "data" / "ytscout.sqlite"
    before = {t: count(db(repo_root), t) for t in ("runs", "video_snapshots", "quota_ledger")}
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: DryRunTransport())
    assert main(["collect", "--own", "--dry-run"]) == EXIT_OK
    after = {t: count(db(repo_root), t) for t in before}
    assert after == before
    assert db(repo_root).execute("SELECT SUM(units_used) FROM quota_ledger").fetchone()[0] == 3
    assert path.is_file()


def test_missing_max_units_refuses(
    repo_root: Path, fake: FakeTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--own"]) == EXIT_ERROR
    assert "--max-units" in capsys.readouterr().err
    assert fake.calls == []
    assert not (repo_root / "data").exists()


def test_quota_exhausted_mid_run_commits_and_exits_3(
    repo_root: Path, fake: FakeTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    # 2 units: channels and the playlist page fit, the videos batch does not.
    assert main(["collect", "--own", "--max-units", "2"]) == EXIT_QUOTA_EXHAUSTED
    conn = db(repo_root)
    assert count(conn, "channels") == 1
    assert count(conn, "channel_snapshots") == 1
    assert count(conn, "videos") == 0
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "quota_exhausted"
    captured = capsys.readouterr()
    assert "channels 1, videos 0" in captured.out
    assert "units this run 2" in captured.out
    assert "run quota cap reached" in captured.err


def test_without_own_flag_names_the_source(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--max-units", "5"]) == EXIT_ERROR
    assert "--own" in capsys.readouterr().err
