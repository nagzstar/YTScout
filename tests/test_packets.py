"""Packets (build, truncate, file) and ``analyse --summaries`` / ``packet`` end to end."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ytscout import packets
from ytscout.analyse import summarise_videos
from ytscout.claude_runner import file_hash
from ytscout.cli import (
    EXIT_CLAUDE_UNAVAILABLE,
    EXIT_ERROR,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
    main,
)
from ytscout.packets import DESCRIPTION_MAX, TRANSCRIPT_MAX, TRUNCATED, truncate, video_packet
from ytscout.settings import find_repo_root
from ytscout.store import connect, repo
from ytscout.store import db as store_db

SRC_ROOT = find_repo_root(Path(__file__).parent)
OWN = "UCown"
COMP = "UCcomp"
REJECTED = "UCrej"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def seed(conn: sqlite3.Connection) -> None:
    """Own channel with 3 videos, an approved competitor with 1, a rejected one with 1."""
    with conn:
        repo.upsert_channel(conn, OWN, role="own", title="Countdown Animal Kingdom")
        repo.add_channel_snapshot(conn, OWN, subs=1200, view_count=5, video_count=3)
        repo.upsert_channel(conn, COMP, role="competitor", title="Rival")
        repo.set_channel_status(conn, COMP, "approved")
        repo.upsert_channel(conn, REJECTED, role="competitor", title="Nope")
        repo.set_channel_status(conn, REJECTED, "rejected")
        for i, (vid, channel) in enumerate(
            [("v1", OWN), ("v2", OWN), ("v3", OWN), ("c1", COMP), ("r1", REJECTED)]
        ):
            repo.upsert_video(
                conn,
                vid,
                channel_id=channel,
                title=f"Top 5 things {i}",
                description="d" * 20,
                tags=["animals", "top5"],
                published_at=f"2026-09-{10 + i:02d}T00:00:00Z",
                duration_s=45,
                is_short=True,
            )
            repo.add_video_snapshot(conn, vid, views=100 + i, likes=10, comments=1)
        # v1: ok transcript; v2: error; v3: never attempted; c1: unavailable; r1: ok.
        repo.put_transcript(
            conn, "v1", language="en", text="Number five: the cheetah.", source="auto", status="ok"
        )
        repo.put_transcript(conn, "v2", language="", text=None, source=None, status="error")
        repo.put_transcript(conn, "c1", language="", text=None, source=None, status="unavailable")
        repo.put_transcript(conn, "r1", language="en", text="hi", source="manual", status="ok")


# --- video_packet -------------------------------------------------------------------------


def test_packet_fields(conn: sqlite3.Connection) -> None:
    seed(conn)
    p = video_packet(conn, "v1")
    assert p["video_id"] == "v1"
    assert p["url"] == "https://www.youtube.com/watch?v=v1"
    assert p["channel"] == {"id": OWN, "title": "Countdown Animal Kingdom", "subs": 1200}
    assert p["title"] == "Top 5 things 0"
    assert p["description"] == "d" * 20
    assert p["tags"] == ["animals", "top5"]
    assert p["duration_s"] == 45 and p["is_short"] is True
    assert p["published_at"] == "2026-09-10T00:00:00Z"
    assert p["stats"]["views"] == 100 and p["stats"]["likes"] == 10
    assert p["stats"]["comments"] == 1 and p["stats"]["captured_at"]
    assert p["transcript"] == "Number five: the cheetah."
    assert p["transcript_status"] == "ok"
    json.dumps(p)  # serialisable


def test_packet_transcript_null_with_status(conn: sqlite3.Connection) -> None:
    seed(conn)
    assert video_packet(conn, "v2")["transcript"] is None
    assert video_packet(conn, "v2")["transcript_status"] == "error"
    assert video_packet(conn, "c1")["transcript_status"] == "unavailable"
    assert video_packet(conn, "v3")["transcript_status"] == "missing"
    with pytest.raises(LookupError):
        video_packet(conn, "nope")


def test_packet_without_snapshots_or_channel_row(conn: sqlite3.Connection) -> None:
    with conn:
        repo.upsert_channel(conn, OWN, role="own")
        repo.upsert_video(conn, "v9", channel_id=OWN, title="t")
    p = video_packet(conn, "v9")
    assert p["channel"] == {"id": OWN, "title": None, "subs": None}
    assert p["stats"] == {"captured_at": None, "views": None, "likes": None, "comments": None}
    assert p["tags"] == [] and p["description"] == "" and p["is_short"] is None


def test_truncation_lengths(conn: sqlite3.Connection) -> None:
    with conn:
        repo.upsert_channel(conn, OWN, role="own")
        repo.upsert_video(conn, "long", channel_id=OWN, title="t", description="x" * 5000)
        repo.put_transcript(
            conn, "long", language="en", text="y" * 20_000, source="auto", status="ok"
        )
    p = video_packet(conn, "long")
    assert len(p["description"]) == DESCRIPTION_MAX == 1000
    assert p["description"].endswith(TRUNCATED)
    assert len(p["transcript"]) == TRANSCRIPT_MAX == 6000
    assert p["transcript"].endswith("…[truncated]")
    assert truncate("short", 20) == "short"
    assert truncate("x" * 20, 20) == "x" * 20
    assert truncate("x" * 21, 20) == "x" * (20 - len(TRUNCATED)) + TRUNCATED
    assert len(truncate("x" * 21, 20)) == 20


# --- write_packet -------------------------------------------------------------------------


def test_write_packet_names_and_numbers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packets, "utc_now", lambda: datetime(2026, 9, 28, 12, tzinfo=UTC))
    directory = tmp_path / "data" / "packets"
    first = packets.write_packet("video_summary", {"a": "é"}, directory)
    second = packets.write_packet("video_summary", {"a": 2}, directory)
    other = packets.write_packet("video", {"a": 3}, directory)
    assert first.name == "2026-09-28-video_summary-1.json"
    assert second.name == "2026-09-28-video_summary-2.json"
    assert other.name == "2026-09-28-video-1.json"
    assert json.loads(first.read_text(encoding="utf-8")) == {"a": "é"}
    (directory / "2026-09-28-video_summary-10.json").write_text("{}", encoding="utf-8")
    assert packets.write_packet("video_summary", {}, directory).name.endswith("-11.json")
    with pytest.raises(ValueError):
        packets.write_packet("Bad Kind", {}, directory)


# --- candidates ---------------------------------------------------------------------------


def test_summary_candidates_rule(conn: sqlite3.Connection) -> None:
    seed(conn)
    # v3 has no transcript attempt; r1 is rejected. Newest first.
    assert repo.summary_candidates(conn, "h1", 40) == ["c1", "v2", "v1"]
    assert repo.summary_candidates(conn, "h1", 2) == ["c1", "v2"]
    with conn:
        repo.put_video_summary(
            conn, "v1", prompt_hash="h1", schema_hash="s", transcript_status="ok", summary={}
        )
        repo.put_video_summary(
            conn, "v2", prompt_hash="h1", schema_hash="s", transcript_status="error", summary={}
        )
    assert repo.summary_candidates(conn, "h1", 40) == ["c1"]
    assert repo.summary_candidates(conn, "h2", 40) == ["c1", "v2", "v1"]
    # v2's transcript arrives: its titles-only summary under h1 is redone.
    with conn:
        repo.put_transcript(conn, "v2", language="en", text="now", source="auto", status="ok")
    assert repo.summary_candidates(conn, "h1", 40) == ["c1", "v2"]
    assert conn.execute("SELECT COUNT(*) FROM transcripts WHERE video_id = 'v2'").fetchone()[0] == 1


# --- summarise_videos with the fake claude -------------------------------------------------


def test_summarise_writes_rows_and_skips_done_ones(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path
) -> None:
    seed(conn)
    packets_dir = tmp_path / "packets"
    counts = summarise_videos(conn, repo_root=SRC_ROOT, packets_dir=packets_dir, limit=2)
    assert (counts.done, counts.candidates, counts.failure) == (2, 2, None)
    assert counts.done_ids == ["c1", "v2"]
    prompt_hash = file_hash(SRC_ROOT / "prompts" / "video_summary.md")
    schema_hash = file_hash(SRC_ROOT / "schemas" / "video_summary.json")
    row = repo.get_video_summary(conn, "c1", prompt_hash)
    assert row is not None
    assert row["schema_hash"] == schema_hash
    assert row["transcript_status"] == "unavailable"
    summary = json.loads(row["summary_json"])
    assert summary["hook_type"] == "question" and summary["claims_count"] == 0
    assert sorted(p.name for p in packets_dir.glob("*.json")) == [
        f"{store_db.utc_now():%Y-%m-%d}-video_summary-1.json",
        f"{store_db.utc_now():%Y-%m-%d}-video_summary-2.json",
    ]
    # The fake saw the packet path of the last call in the prompt.
    argv = fake_claude.record()["argv"]
    assert argv[1].endswith("video_summary-2.json")

    counts = summarise_videos(conn, repo_root=SRC_ROOT, packets_dir=packets_dir)
    assert (counts.done, counts.candidates) == (1, 1) and counts.done_ids == ["v1"]
    counts = summarise_videos(conn, repo_root=SRC_ROOT, packets_dir=packets_dir)
    assert (counts.done, counts.candidates) == (0, 0)


def test_summarise_redoes_under_a_new_prompt_hash(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path
) -> None:
    seed(conn)
    summarise_videos(conn, repo_root=SRC_ROOT, packets_dir=tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM video_summaries").fetchone()[0] == 3
    edited = tmp_path / "video_summary.md"
    edited.write_text("Summarise the packet. Be terse.\n", encoding="utf-8")
    counts = summarise_videos(conn, repo_root=SRC_ROOT, packets_dir=tmp_path, prompt_path=edited)
    assert counts.done == 3
    assert conn.execute("SELECT COUNT(*) FROM video_summaries").fetchone()[0] == 6
    hashes = {r[0] for r in conn.execute("SELECT DISTINCT prompt_hash FROM video_summaries")}
    assert hashes == {file_hash(edited), file_hash(SRC_ROOT / "prompts" / "video_summary.md")}


def test_summarise_stops_at_first_failure_and_keeps_earlier_rows(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(conn)
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", "garbage")
    counts = summarise_videos(conn, repo_root=SRC_ROOT, packets_dir=tmp_path)
    assert counts.done == 0 and counts.failed_video_id == "c1"
    assert "no JSON result object" in (counts.failure or "")
    assert conn.execute("SELECT COUNT(*) FROM video_summaries").fetchone()[0] == 0


# --- the CLI ------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp repo root with settings, the real prompt and schema, and a seeded DB."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(f"own_channel_id: {OWN}\n", encoding="utf-8")
    for sub in ("prompts", "schemas"):
        shutil.copytree(SRC_ROOT / sub, tmp_path / sub)
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    c = connect(tmp_path / "data" / "ytscout.sqlite")
    seed(c)
    c.close()
    return tmp_path


def _count(root: Path) -> int:
    c = sqlite3.connect(root / "data" / "ytscout.sqlite")
    try:
        return c.execute("SELECT COUNT(*) FROM video_summaries").fetchone()[0]
    finally:
        c.close()


def test_cli_packet_writes_and_prints_path(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["packet", "--video", "v1"]) == EXIT_OK
    printed = Path(capsys.readouterr().out.strip())
    assert printed.parent == repo_root / "data" / "packets"
    assert printed.name.endswith("-video-1.json")
    assert json.loads(printed.read_text(encoding="utf-8"))["video_id"] == "v1"
    assert main(["packet", "--video", "zzz"]) == EXIT_ERROR
    assert "no video 'zzz'" in capsys.readouterr().err


def test_cli_analyse_summaries_exits_5_without_claude(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", "")
    assert main(["analyse", "--summaries"]) == EXIT_CLAUDE_UNAVAILABLE
    assert "claude unavailable" in capsys.readouterr().err
    assert _count(repo_root) == 0
    assert not (repo_root / "data" / "packets").exists()


def test_cli_analyse_summaries_exits_5_when_too_old(
    repo_root: Path,
    fake_claude,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.0.9 (Claude Code)")
    assert main(["analyse", "--summaries"]) == EXIT_CLAUDE_UNAVAILABLE
    assert "older than the minimum 2.1.259" in capsys.readouterr().err
    assert _count(repo_root) == 0


def test_cli_analyse_summaries_runs_and_then_has_nothing_to_do(
    repo_root: Path, fake_claude, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["analyse", "--summaries", "--limit", "1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "1 of 1 candidates summarised" in out
    assert "  c1: question" in out
    assert _count(repo_root) == 1
    assert Path(fake_claude.record()["cwd"]).resolve() == repo_root.resolve()
    assert main(["analyse", "--summaries"]) == EXIT_OK
    assert "2 of 2 candidates summarised" in capsys.readouterr().out
    assert main(["analyse", "--summaries"]) == EXIT_OK
    assert "0 of 0 candidates summarised" in capsys.readouterr().out
    assert _count(repo_root) == 3
    c = sqlite3.connect(repo_root / "data" / "ytscout.sqlite")
    kinds = c.execute("SELECT kind, status FROM runs ORDER BY id").fetchall()
    c.close()
    assert kinds == [("analyse_summaries", "ok")] * 3


def test_cli_analyse_redoes_under_new_hash(repo_root: Path, fake_claude) -> None:
    assert main(["analyse", "--summaries"]) == EXIT_OK
    assert _count(repo_root) == 3
    prompt = repo_root / "prompts" / "video_summary.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nBe terse.\n", encoding="utf-8")
    assert main(["analyse", "--summaries"]) == EXIT_OK
    assert _count(repo_root) == 6


def test_cli_analyse_failure_exit_codes(
    repo_root: Path,
    fake_claude,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "2")
    assert main(["analyse", "--summaries"]) == EXIT_CLAUDE_UNAVAILABLE
    assert "claude failed on c1: claude exited 2" in capsys.readouterr().err
    assert _count(repo_root) == 0
    c = sqlite3.connect(repo_root / "data" / "ytscout.sqlite")
    assert c.execute("SELECT status FROM runs").fetchone()[0] == "error"
    c.close()


def test_cli_analyse_dry_run_lists_and_calls_nothing(
    repo_root: Path, fake_claude, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["analyse", "--summaries", "--dry-run", "--limit", "2"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "  c1\n  v2\n" in out and "candidates: 2" in out
    assert "claude: ok" in out
    assert "--permission-prompts none" in out and "--bare" not in out
    assert _count(repo_root) == 0
    assert fake_claude.record()["argv"] == ["--version"]  # only the version check ran


def test_cli_analyse_competitors_is_still_a_stub(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["analyse", "--competitors"]) == EXIT_NOT_IMPLEMENTED
    assert "not implemented yet (issue 017)" in capsys.readouterr().err
    assert main(["analyse"]) == EXIT_ERROR
