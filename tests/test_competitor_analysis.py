"""``analyse --competitors`` (017): the comparison packet, grounding, the row, the Findings."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from ytscout import packets
from ytscout.analyse import (
    STATUS_OK,
    STATUS_PENDING,
    analyse_competitors,
    competitor_paths,
    ground_analysis,
)
from ytscout.claude_runner import file_hash, validate
from ytscout.cli import EXIT_CLAUDE_UNAVAILABLE, EXIT_OK, main
from ytscout.dashboard.build import build, load
from ytscout.packets import (
    COMPETITOR_PACKET_MAX_BYTES,
    COMPETITOR_VIDEOS,
    COMPETITOR_VIDEOS_REDUCED,
    competitor_packet,
)
from ytscout.settings import find_repo_root
from ytscout.store import connect, read_copy, repo

SRC_ROOT = find_repo_root(Path(__file__).parent)
PROMPT, SCHEMA = competitor_paths(SRC_ROOT)
OWN = "UCown"
COMP = "UCcomp"
COMP2 = "UCcomp2"
WATCH = "UCwatch"
REJECTED = "UCrej"
UNKNOWN_VIDEO = "zzz_not_in_packet"
FAKE_CLAUDE_TEMPLATE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "session_id": "canned",
    "total_cost_usd": 0.05,
    "usage": {"input_tokens": 900, "output_tokens": 400},
}


def summary(i: int, tag: str) -> dict:
    return {
        "hook_type": "countdown_tease",
        "structure": f"countdown of five {tag}",
        "topic_tags": [tag, "animals"],
        "title_formula": "Top 5 <animals>",
        "pacing_note": "fast",
        "claims_count": i,
        "unique_angle": f"angle {i}",
        "one_line_summary": f"five {tag} number {i}",
    }


def seed(conn: sqlite3.Connection, *, own_videos: int = 4, comp_videos: int = 3) -> None:
    """Own + two approved competitors with summarised videos; a watch and a rejected one too.

    Own videos ``o1..oN`` (views 100, 200, ...), ``c1..cN`` for ``UCcomp``, ``d1..dN`` for
    ``UCcomp2``; ``w1`` (watch) and ``r1`` (rejected) are summarised but must not appear.
    ``o1`` has two summaries; the newer one (``claims_count`` 99) wins.
    """
    with conn:
        repo.upsert_channel(conn, OWN, role="own", title="Countdown Animal Kingdom")
        repo.add_channel_snapshot(conn, OWN, subs=1200, view_count=5, video_count=own_videos)
        repo.upsert_channel(conn, COMP, role="competitor", title="Rival")
        repo.set_channel_status(conn, COMP, "approved")
        repo.add_channel_snapshot(conn, COMP, subs=50_000, view_count=5, video_count=3)
        repo.upsert_channel(conn, COMP2, role="competitor", title="Beast Facts")
        repo.set_channel_status(conn, COMP2, "approved")
        repo.upsert_channel(conn, WATCH, role="competitor", title="Watched")
        repo.set_channel_status(conn, WATCH, "watch")
        repo.upsert_channel(conn, REJECTED, role="competitor", title="Nope")
        repo.set_channel_status(conn, REJECTED, "rejected")
        plan = (
            [(f"o{i}", OWN, "big-cats") for i in range(1, own_videos + 1)]
            + [(f"c{i}", COMP, "ocean") for i in range(1, comp_videos + 1)]
            + [(f"d{i}", COMP2, "ocean") for i in range(1, comp_videos + 1)]
            + [("w1", WATCH, "birds"), ("r1", REJECTED, "birds")]
        )
        for n, (vid, channel, tag) in enumerate(plan, start=1):
            repo.upsert_video(
                conn,
                vid,
                channel_id=channel,
                title=f"Top 5 {tag} {vid}",
                description="d",
                tags=[tag],
                published_at=f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}T00:00:00Z",
                duration_s=45,
                is_short=True,
            )
            repo.add_video_snapshot(conn, vid, views=100 * n, likes=1, comments=0)
            repo.put_transcript(conn, vid, language="", text=None, source=None, status="error")
            repo.put_video_summary(
                conn,
                vid,
                prompt_hash="old",
                schema_hash="s",
                transcript_status="error",
                summary=summary(n, tag),
            )
        repo.put_video_summary(
            conn,
            "o1",
            prompt_hash="new",
            schema_hash="s",
            transcript_status="error",
            summary=summary(99, "big-cats"),
        )
        repo.add_channel_metrics(
            conn,
            OWN,
            window="90d",
            fmt="shorts",
            metrics={"views_median": 250, "outlier_ids": ["o4"], "uploads_per_week": 1.0},
        )
        repo.add_channel_metrics(
            conn, OWN, window="365d", fmt="shorts", metrics={"views_median": 999}
        )
        repo.add_channel_metrics(conn, COMP, window="90d", fmt="long", metrics={"views_median": 1})


def canned_analysis(**overrides: object) -> dict:
    """A full analysis citing packet ids plus one video id that is not in the packet."""
    analysis = {
        "per_competitor": [
            {
                "channel_id": COMP,
                "does_consistently": [
                    {"text": "Ocean predators every time.", "evidence_video_ids": ["c1", "c2"]}
                ],
                "they_do_we_dont": [
                    {"text": "Shock stat openers.", "evidence_video_ids": ["c3", UNKNOWN_VIDEO]}
                ],
                "we_do_they_dont": [],
            },
            {
                "channel_id": COMP2,
                "does_consistently": [],
                "they_do_we_dont": [],
                "we_do_they_dont": [{"text": "Countdown tease.", "evidence_video_ids": ["o1"]}],
            },
        ],
        "topic_gaps": [
            {
                "topic": "ocean predators",
                "covered_by_channel_ids": [COMP, COMP2],
                "evidence_video_ids": ["c3", "d3"],
                "why": "Both rivals' top videos are ocean topics.",
            }
        ],
        "our_weaknesses": [
            {
                "pattern": "Early videos lack a tease.",
                "below_median_video_ids": ["o1"],
                "above_median_video_ids": ["o4"],
            }
        ],
        "next_videos": [
            {
                "title": f"Top 5 Deadliest Ocean Hunters #{i}",
                "angle": "five predators ranked by kill rate",
                "evidence_video_ids": ["c3", "d3"] if i < 5 else [UNKNOWN_VIDEO],
                "rationale": "gap plus own above-median pattern",
            }
            for i in range(1, 6)
        ],
        "meta": {"confidence": "medium", "caveats": ["No transcripts were available."]},
    }
    analysis.update(overrides)
    return analysis


def canned_output(analysis: dict) -> str:
    return json.dumps({**FAKE_CLAUDE_TEMPLATE, "structured_output": analysis}) + "\n"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


# --- packet ----------------------------------------------------------------------------------


def test_packet_holds_own_then_approved_with_latest_summaries(conn: sqlite3.Connection) -> None:
    seed(conn)
    packet = competitor_packet(conn)
    assert packet["own_channel_id"] == OWN
    assert [c["id"] for c in packet["channels"]] == [OWN, COMP2, COMP]  # own, then by title
    assert [c["role"] for c in packet["channels"]] == ["own", "competitor", "competitor"]
    own = packet["channels"][0]
    assert own["title"] == "Countdown Animal Kingdom" and own["subs"] == 1200
    assert [v["video_id"] for v in own["videos"]] == ["o4", "o3", "o2", "o1"]  # newest first
    assert own["views_median"] == 250.0
    # The 90d/shorts metrics, without the outlier id list.
    assert own["metrics"] == {"views_median": 250, "uploads_per_week": 1.0}
    assert packet["channels"][2]["metrics"] is None  # only a long-form row exists
    o1 = own["videos"][-1]
    assert o1["summary"]["claims_count"] == 99  # the newer summary wins
    assert o1["views"] == 100 and o1["title"] == "Top 5 big-cats o1"
    assert o1["transcript_status"] == "error" and o1["duration_s"] == 45
    assert set(o1["summary"]) == {
        "hook_type",
        "structure",
        "topic_tags",
        "title_formula",
        "pacing_note",
        "claims_count",
        "unique_angle",
        "one_line_summary",
    }
    assert packet["meta"]["videos_per_channel"] == COMPETITOR_VIDEOS
    assert packet["meta"]["reduced"] is False and packet["meta"]["note"] is None
    assert packets.packet_video_ids(packet) == {"o1", "o2", "o3", "o4", "c1", "c2", "c3"} | {
        "d1",
        "d2",
        "d3",
    }
    assert packets.packet_channel_ids(packet) == {OWN, COMP, COMP2}
    assert "w1" not in json.dumps(packet) and "r1" not in json.dumps(packet)


def test_packet_caps_videos_and_reduces_when_large(conn: sqlite3.Connection) -> None:
    seed(conn, own_videos=20)
    packet = competitor_packet(conn)
    assert len(packet["channels"][0]["videos"]) == COMPETITOR_VIDEOS
    # Bloat every own summary until the packet passes the cap: 10 per channel, noted.
    with conn:
        for i in range(1, 21):
            repo.put_video_summary(
                conn,
                f"o{i}",
                prompt_hash="huge",
                schema_hash="s",
                transcript_status="error",
                summary={**summary(i, "x"), "one_line_summary": "y" * 12_000},
            )
    packet = competitor_packet(conn)
    assert packet["meta"]["reduced"] is True
    assert packet["meta"]["videos_per_channel"] == COMPETITOR_VIDEOS_REDUCED
    assert "cut from 15 to 10" in packet["meta"]["note"]
    assert len(packet["channels"][0]["videos"]) == COMPETITOR_VIDEOS_REDUCED
    assert len(json.dumps(packet).encode()) < COMPETITOR_PACKET_MAX_BYTES


def test_packet_with_no_channels_is_empty(conn: sqlite3.Connection) -> None:
    packet = competitor_packet(conn)
    assert packet["own_channel_id"] is None and packet["channels"] == []


# --- schema and prompt -----------------------------------------------------------------------


def test_schema_has_the_specified_shape() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    props = schema["properties"]
    assert set(schema["required"]) == {
        "per_competitor",
        "topic_gaps",
        "our_weaknesses",
        "next_videos",
        "meta",
    }
    assert props["next_videos"]["minItems"] == 5 and props["next_videos"]["maxItems"] == 5
    assert props["topic_gaps"]["items"]["properties"]["covered_by_channel_ids"]["minItems"] == 2
    for section in ("does_consistently", "they_do_we_dont", "we_do_they_dont"):
        item = props["per_competitor"]["items"]["properties"][section]["items"]
        assert item["properties"]["text"]["maxLength"] == 200
        assert "evidence_video_ids" in item["required"]
    assert props["meta"]["properties"]["confidence"]["enum"] == ["low", "medium", "high"]
    validate(canned_analysis(), schema)  # the canned analysis is what Claude may return
    text = PROMPT.read_text(encoding="utf-8")
    assert "Top-5 countdown Shorts" in text and "faceless" in text and "no sign-off" in text
    assert "Never invent" in text


# --- grounding -------------------------------------------------------------------------------


def test_ground_analysis_drops_unknown_ids_and_counts_them() -> None:
    analysis = canned_analysis()
    analysis["per_competitor"].append(
        {
            "channel_id": "UCghost",
            "does_consistently": [],
            "they_do_we_dont": [],
            "we_do_they_dont": [],
        }
    )
    analysis["topic_gaps"][0]["covered_by_channel_ids"] = [COMP, "UCghost", COMP2]
    known_videos = {"o1", "o2", "o3", "o4", "c1", "c2", "c3", "d1", "d2", "d3"}
    grounded, dropped_videos, dropped_channels = ground_analysis(
        analysis, known_videos, {OWN, COMP, COMP2}
    )
    assert dropped_videos == [UNKNOWN_VIDEO] and dropped_channels == ["UCghost"]
    assert UNKNOWN_VIDEO not in json.dumps(grounded) and "UCghost" not in json.dumps(grounded)
    assert grounded["per_competitor"][0]["they_do_we_dont"][0]["evidence_video_ids"] == ["c3"]
    assert grounded["next_videos"][4]["evidence_video_ids"] == []
    assert grounded["topic_gaps"][0]["covered_by_channel_ids"] == [COMP, COMP2]
    assert [c["channel_id"] for c in grounded["per_competitor"]] == [COMP, COMP2]
    assert grounded["meta"]["caveats"] == [
        "No transcripts were available.",
        "1 cited video id(s) were not in the packet and were dropped.",
        "1 cited channel id(s) were not in the packet and were dropped.",
    ]
    assert UNKNOWN_VIDEO in json.dumps(analysis)  # the input was not mutated


def test_ground_analysis_with_nothing_to_drop_adds_no_caveat() -> None:
    analysis = canned_analysis()
    analysis["per_competitor"][0]["they_do_we_dont"][0]["evidence_video_ids"] = ["c3"]
    analysis["next_videos"][4]["evidence_video_ids"] = ["o1"]
    grounded, dropped_videos, dropped_channels = ground_analysis(
        analysis, {"o1", "c1", "c2", "c3", "d3", "o4"}, {COMP, COMP2}
    )
    assert (dropped_videos, dropped_channels) == ([], [])
    assert grounded["meta"]["caveats"] == ["No transcripts were available."]


# --- analyse_competitors ---------------------------------------------------------------------


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM competitor_analyses ORDER BY id").fetchall()


def test_analyse_writes_a_grounded_ok_row(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(conn)
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned_output(canned_analysis()))
    outcome = analyse_competitors(conn, repo_root=SRC_ROOT, packets_dir=tmp_path / "packets")
    assert outcome.status == STATUS_OK and outcome.failure is None
    assert (outcome.channels, outcome.videos) == (3, 10)
    assert outcome.dropped_video_ids == [UNKNOWN_VIDEO] and outcome.dropped_channel_ids == []
    assert outcome.usage == {"input_tokens": 900, "output_tokens": 400}
    assert outcome.packet_path is not None and outcome.packet_path.is_file()
    assert outcome.packet_path.name.endswith("-competitors-1.json")
    rows = _rows(conn)
    assert len(rows) == 1 and rows[0]["id"] == outcome.row_id
    row = rows[0]
    assert row["status"] == "ok"
    assert row["prompt_hash"] == file_hash(PROMPT)
    assert row["schema_version"] == file_hash(SCHEMA)
    assert row["packet_path"] == str(outcome.packet_path)
    stored = json.loads(row["result_json"])
    assert UNKNOWN_VIDEO not in row["result_json"]  # never reaches the DB
    assert stored["next_videos"][4]["evidence_video_ids"] == []
    assert len(stored["next_videos"]) == 5
    assert (
        stored["meta"]["caveats"][-1]
        == "1 cited video id(s) were not in the packet and were dropped."
    )
    argv = fake_claude.record()["argv"]
    assert argv[1].endswith(str(outcome.packet_path))
    assert argv[argv.index("--json-schema") + 1] == SCHEMA.read_text(encoding="utf-8")


def test_analyse_records_pending_when_claude_fails(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(conn)
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")
    outcome = analyse_competitors(conn, repo_root=SRC_ROOT, packets_dir=tmp_path / "packets")
    assert outcome.status == STATUS_PENDING
    assert outcome.failure is not None and "claude exited 3" in outcome.failure
    rows = _rows(conn)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert json.loads(rows[0]["result_json"])["error"] == outcome.failure
    assert rows[0]["packet_path"] == str(outcome.packet_path)
    assert rows[0]["prompt_hash"] == file_hash(PROMPT)


def test_analyse_accepts_the_fakes_generated_output(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path
) -> None:
    """The fake fills the schema mechanically ("fake" ids): all of them are dropped."""
    seed(conn)
    outcome = analyse_competitors(conn, repo_root=SRC_ROOT, packets_dir=tmp_path / "packets")
    assert outcome.status == STATUS_OK
    assert outcome.dropped_video_ids == ["fake"] and outcome.dropped_channel_ids == ["fake"]
    stored = json.loads(_rows(conn)[0]["result_json"])
    assert stored["per_competitor"] == [] and len(stored["next_videos"]) == 5


# --- CLI -------------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp repo root with settings, the real prompts and schemas, and a seeded DB."""
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


def _db_rows(root: Path, sql: str) -> list[tuple]:
    c = sqlite3.connect(root / "data" / "ytscout.sqlite")
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_cli_competitors_writes_one_row_and_the_dashboard_shows_it(
    repo_root: Path,
    fake_claude,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned_output(canned_analysis()))
    assert main(["analyse", "--competitors"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "analyse --competitors: 3 channels, 10 summarised videos, packet " in out
    assert "row 1 ok (" in out and "1 unknown id(s) dropped" in out
    assert f"dropped ids not in the packet: {UNKNOWN_VIDEO}" in out
    assert _db_rows(repo_root, "SELECT status FROM competitor_analyses") == [("ok",)]
    assert _db_rows(repo_root, "SELECT kind, status FROM runs") == [("analyse_competitors", "ok")]
    assert Path(fake_claude.record()["cwd"]).resolve() == repo_root.resolve()
    assert len(list((repo_root / "data" / "packets").glob("*-competitors-*.json"))) == 1

    assert main(["dashboard"]) == EXIT_OK
    html = (repo_root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert html.count("Top 5 Deadliest Ocean Hunters #") == 5
    assert html.count('class="panel next-video"') == 5
    assert 'href="https://www.youtube.com/watch?v=c3">Top 5 ocean c3</a>' in html
    assert 'href="https://www.youtube.com/watch?v=o4">Top 5 big-cats o4</a>' in html
    assert UNKNOWN_VIDEO not in html
    assert "analysis pending" not in html
    assert "confidence <strong>medium</strong>" in html
    assert "1 cited video id(s) were not in the packet" in html
    assert f"<code>{file_hash(PROMPT)}</code>" in html
    assert "<h4>Rival</h4>" in html and "<h4>Beast Facts</h4>" in html
    assert "Rival, Beast Facts" in html  # covered_by on the topic gap


def test_cli_competitors_dry_run_calls_nothing_and_writes_nothing(
    repo_root: Path, fake_claude, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["analyse", "--competitors", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "dry run: the competitor packet" in out
    assert f"  {OWN} Countdown Animal Kingdom: 4 summarised videos, role own" in out
    assert "channels: 3; videos: 10; packet:" in out
    assert "claude: ok" in out
    assert fake_claude.record()["argv"] == ["--version"]  # only the version check ran
    assert _db_rows(repo_root, "SELECT COUNT(*) FROM competitor_analyses") == [(0,)]
    assert not (repo_root / "data" / "packets").exists()


def test_cli_competitors_without_claude_writes_pending_and_exits_5(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", "")
    assert main(["analyse", "--competitors"]) == EXIT_CLAUDE_UNAVAILABLE
    captured = capsys.readouterr()
    assert "claude unavailable" in captured.err and "row 1 pending" in captured.out
    assert _db_rows(repo_root, "SELECT status, packet_path FROM competitor_analyses") == [
        ("pending", None)
    ]
    assert _db_rows(repo_root, "SELECT kind, status FROM runs") == [
        ("analyse_competitors", "error")
    ]

    assert main(["dashboard"]) == EXIT_OK
    html = (repo_root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "analysis pending — <code>claude</code> was unavailable at " in html
    assert "not on PATH" in html


def test_cli_competitors_pending_when_the_call_fails(
    repo_root: Path, fake_claude, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", "not json at all")
    assert main(["analyse", "--competitors"]) == EXIT_CLAUDE_UNAVAILABLE
    rows = _db_rows(repo_root, "SELECT status, packet_path FROM competitor_analyses")
    assert len(rows) == 1 and rows[0][0] == "pending" and rows[0][1].endswith("-competitors-1.json")
    assert _db_rows(repo_root, "SELECT status FROM runs") == [("error",)]


def test_cli_both_stages_run_summaries_then_competitors(
    repo_root: Path, fake_claude, capsys: pytest.CaptureFixture[str]
) -> None:
    # Every seeded video already has a summary under an old hash: all 12 are candidates
    # under the real prompt hash, then the comparison runs once.
    assert main(["analyse", "--summaries", "--competitors", "--limit", "2"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "2 of 2 candidates summarised" in out
    assert "analyse --competitors: 3 channels" in out
    assert _db_rows(repo_root, "SELECT kind, status FROM runs ORDER BY id") == [
        ("analyse_summaries", "ok"),
        ("analyse_competitors", "ok"),
    ]


def test_cli_both_stages_skip_competitors_when_summaries_fail(
    repo_root: Path,
    fake_claude,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "2")
    assert main(["analyse", "--summaries", "--competitors"]) == EXIT_CLAUDE_UNAVAILABLE
    assert "skipping --competitors" in capsys.readouterr().err
    assert _db_rows(repo_root, "SELECT COUNT(*) FROM competitor_analyses") == [(0,)]


# --- dashboard -------------------------------------------------------------------------------


def test_dashboard_falls_back_to_the_id_and_reads_the_latest_row(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    seed(conn)
    with conn:
        repo.upsert_video(conn, "untitled", channel_id=COMP, title=None)
        repo.put_competitor_analysis(
            conn,
            prompt_hash="p0",
            schema_hash="s0",
            packet_path=None,
            result=None,
            status="pending",
            run_at="2026-09-01T10:00:00Z",
        )
        analysis = canned_analysis()
        analysis["next_videos"][4]["evidence_video_ids"] = ["untitled", "ghost"]
        repo.put_competitor_analysis(
            conn,
            prompt_hash="p1",
            schema_hash="s1",
            packet_path="x.json",
            result=analysis,
            status="ok",
            run_at="2026-09-08T10:00:00Z",
        )
    dash = load(read_copy(tmp_path / "t.sqlite"))
    assert dash.findings is not None
    assert dash.findings["status"] == "ok" and dash.findings["run_at"] == "2026-09-08T10:00:00Z"
    assert dash.findings["titles"]["untitled"] is None and "ghost" not in dash.findings["titles"]
    out = tmp_path / "index.html"
    build(read_copy(tmp_path / "t.sqlite"), out)
    html = out.read_text(encoding="utf-8")
    assert 'href="https://www.youtube.com/watch?v=untitled">untitled</a>' in html
    assert 'href="https://www.youtube.com/watch?v=ghost">ghost</a>' in html
    assert "Run 2026-09-08T10:00:00Z" in html and "<code>p1</code>" in html


def test_dashboard_pending_note_shows_the_clock(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with conn:
        repo.put_competitor_analysis(
            conn,
            prompt_hash="p0",
            schema_hash="s0",
            packet_path=None,
            result={"error": "claude exited 2"},
            status="pending",
            run_at="2026-09-01T07:05:00Z",
        )
    out = tmp_path / "index.html"
    build(read_copy(tmp_path / "t.sqlite"), out)
    html = out.read_text(encoding="utf-8")
    assert "was unavailable at 07:05 UTC (claude exited 2)." in html
