"""``scout tag`` and ``score --niches``: from DB rows to ``niche_scores`` (issue 024).

The centre is issue 023's worked example rebuilt as DB rows (six channels, their videos
and snapshots, one tagged niche, one own Short with Analytics): ``score --niches`` must
write exactly the 023 numbers. The RPM table and the pipeline coverage are the example's,
passed in directly or written as the YAML files the CLI reads.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from test_scoring import CFG, COVERAGE, NOW, REQUIRED, STEPS, TABLE, A, B, C, D, E, F, sig3

from ytscout import cli
from ytscout.audit import Step, StepCoverage, load_steps
from ytscout.cli import EXIT_CLAUDE_UNAVAILABLE, EXIT_OK, main
from ytscout.scoring.effort import manual_hours_per_video, specific_footage
from ytscout.scoring.types import ChannelSample
from ytscout.scout import score as scout_score
from ytscout.scout import tag as scout_tag
from ytscout.store import connect, repo, to_utc_iso

REPO_ROOT = Path(__file__).resolve().parents[1]
OWN = "UCown"
CHANNELS = {"A": A, "B": B, "C": C, "D": D, "E": E, "F": F}


# ---------------------------------------------------------------- building the example


def add_channel(conn: sqlite3.Connection, ch: ChannelSample, niche_id: int) -> None:
    cid = f"UC{ch.channel_id}"
    repo.upsert_channel(conn, cid, role="niche_sample", created_at=ch.created_at)
    # An older snapshot first: the scorer must read the latest one.
    repo.add_channel_snapshot(
        conn, cid, subs=1, view_count=None, video_count=None, captured_at=NOW - timedelta(days=30)
    )
    repo.add_channel_snapshot(
        conn, cid, subs=ch.subs, view_count=None, video_count=None, captured_at=NOW
    )
    for i, v in enumerate(ch.videos):
        vid = f"{cid}-v{i}"
        repo.upsert_video(
            conn,
            vid,
            channel_id=cid,
            title=f"{ch.channel_id} animals video {i} ({v.views} views)",
            published_at=v.published_at,
            duration_s=v.duration_s,
        )
        repo.add_video_snapshot(
            conn, vid, views=1, likes=None, comments=None, captured_at=NOW - timedelta(days=1)
        )
        repo.add_video_snapshot(
            conn, vid, views=v.views, likes=None, comments=None, captured_at=NOW
        )
    repo.put_niche_channel(conn, niche_id, cid, is_small=None)


def add_niche(
    conn: sqlite3.Connection,
    *,
    topic: str = "dangerous-animals",
    fmt: str = "shorts",
    status: str = "validated",
    tagged: bool = True,
    footage: bool = False,
) -> int:
    niche_id = repo.insert_niche(
        conn,
        fmt=fmt,
        topic=topic,
        topic_category="animals_nature",
        label=f"Top 5 countdown: {topic}",
        source="seed",
        queries=["q1", "q2", "q3"],
        required_steps=["visuals_stock"],
        meta={"why_ai_able": "stock and TTS"},
        status=status,
    )
    if tagged:
        repo.set_niche_tags(
            conn,
            niche_id,
            required_steps=REQUIRED,
            needs_specific_footage=footage,
            notes="n",
            prompt_hash="p" * 12,
            schema_hash="s" * 12,
        )
    return niche_id


def add_own_analytics(conn: sqlite3.Connection, rpm: float = 0.10) -> None:
    repo.upsert_channel(conn, OWN, role="own")
    repo.upsert_video(conn, "own1", channel_id=OWN, published_at=NOW, duration_s=30)
    repo.upsert_own_analytics(
        conn, "own1", "2025-07-28", "2026-08-31", views=5_000, rpm_usd=rpm, est_revenue_usd=0.5
    )


def build_example(conn: sqlite3.Connection, *, own: bool = True, **niche) -> int:
    with conn:
        niche_id = add_niche(conn, **niche)
        for ch in CHANNELS.values():
            add_channel(conn, ch, niche_id)
        if own:
            add_own_analytics(conn)
    return niche_id


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def run_score(conn: sqlite3.Connection) -> scout_score.NicheScoreResult:
    return scout_score.score_niches(
        conn, cfg=CFG, rpm=TABLE, steps=STEPS, coverage=COVERAGE, usd_gbp=0.78, now=NOW
    )


def score_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM niche_scores ORDER BY id").fetchall()


def assert_worked_example(row: sqlite3.Row | dict) -> None:
    """Every bold number of issue 023's worked example, to 3 significant figures."""
    assert sig3(row["small_outlier_rate"]) == 0.667
    assert sig3(row["newcomer_view_share"]) == 0.113
    assert sig3(row["concentration"]) == 0.901
    assert sig3(row["opportunity"]) == 0.387
    assert sig3(row["newcomer_monthly_views_p25"]) == 7_500
    assert sig3(row["newcomer_monthly_views_p50"]) == 13_300
    assert sig3(row["newcomer_monthly_views_p75"]) == 26_700
    assert sig3(row["rpm_gbp"]) == 0.078
    assert sig3(row["est_monthly_gbp"]) == 1.04
    assert sig3(row["manual_hours_per_month"]) == 18.0
    assert sig3(row["score"]) == 0.0578


# ---------------------------------------------------------------- build_sample / calibration


def test_build_sample_reads_latest_snapshots(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn)
    sample = scout_score.build_sample(conn, niche_id, CFG, now=NOW)
    assert sample.fmt == "shorts" and sample.now == NOW
    by_id = {c.channel_id: c for c in sample.channels}
    assert set(by_id) == {f"UC{k}" for k in CHANNELS}
    for key, ch in CHANNELS.items():
        got = by_id[f"UC{key}"]
        assert got.subs == ch.subs
        assert got.created_at == ch.created_at
        assert sorted(v.views for v in got.videos) == sorted(v.views for v in ch.videos)


def test_build_sample_skips_videos_without_a_snapshot(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn)
    with conn:
        repo.upsert_video(conn, "nosnap", channel_id="UCA", published_at=NOW, duration_s=20)
    sample = scout_score.build_sample(conn, niche_id, CFG, now=NOW)
    (a,) = [c for c in sample.channels if c.channel_id == "UCA"]
    assert len(a.videos) == len(A.videos)


def test_calibration_from_db_is_the_median_of_qualifying_own_shorts(
    conn: sqlite3.Connection,
) -> None:
    with conn:
        add_own_analytics(conn, rpm=0.10)
        rows = [
            ("own2", 30, "2026-08-31", 2_000, 0.20),  # counts
            ("own3", 30, "2026-08-31", 3_000, 0.12),  # counts → median of .10 .12 .20 = .12
            ("own4", 30, "2026-08-31", 999, 9.0),  # under 1,000 views
            ("own5", 600, "2026-08-31", 9_000, 9.0),  # long-form
            ("own6", 30, "2026-05-01", 9_000, 9.0),  # window ended > 90 days ago
        ]
        for vid, dur, end, views, rpm in rows:
            repo.upsert_video(conn, vid, channel_id=OWN, published_at=NOW, duration_s=dur)
            repo.upsert_own_analytics(conn, vid, "2025-07-28", end, views=views, rpm_usd=rpm)
        # own1's older, wider window is ignored in favour of its newest one.
        repo.upsert_own_analytics(conn, "own1", "2025-01-01", "2026-01-01", views=9, rpm_usd=5.0)
    assert scout_score.own_rpm_usd(conn, now=NOW, shorts_max_seconds=180) == pytest.approx(0.12)
    cal = scout_score.calibration_from_db(conn, TABLE, now=NOW, shorts_max_seconds=180)
    assert cal == pytest.approx(0.12 / 0.08)


def test_calibration_from_db_is_none_without_analytics(conn: sqlite3.Connection) -> None:
    assert scout_score.calibration_from_db(conn, TABLE, now=NOW, shorts_max_seconds=180) is None


# ---------------------------------------------------------------- score_niches


def test_worked_example_end_to_end_through_the_db(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn)
    result = run_score(conn)
    assert result.skipped_untagged == 0
    (row,) = score_rows(conn)
    assert row["niche_id"] == niche_id
    assert row["scored_at"] == to_utc_iso(NOW)
    assert_worked_example(row)
    assert json.loads(row["confidence_flags_json"]) == ["low_confidence"]
    assert repo.get_niche(conn, niche_id)["status"] == "scored"


def test_rescoring_appends_a_new_row(conn: sqlite3.Connection) -> None:
    build_example(conn)
    run_score(conn)
    run_score(conn)
    rows = score_rows(conn)
    assert len(rows) == 2
    assert [r["score"] for r in rows][0] == rows[1]["score"]


def test_uncalibrated_when_own_analytics_is_empty(conn: sqlite3.Connection) -> None:
    build_example(conn, own=False)
    run_score(conn)
    (row,) = score_rows(conn)
    assert sig3(row["rpm_gbp"]) == 0.0624  # 0.08 × 1.0 × 0.78
    assert json.loads(row["confidence_flags_json"]) == ["low_confidence", "uncalibrated"]


def test_untagged_niche_is_skipped_and_counted(conn: sqlite3.Connection) -> None:
    build_example(conn)
    with conn:
        untagged = add_niche(conn, topic="other", tagged=False)
    result = run_score(conn)
    assert result.skipped_untagged == 1
    assert [r["niche_id"] for r in score_rows(conn)] != [untagged]
    assert len(score_rows(conn)) == 1
    assert repo.get_niche(conn, untagged)["status"] == "validated"


def test_proposed_and_shelved_niches_are_not_scored_and_track_keeps_its_status(
    conn: sqlite3.Connection,
) -> None:
    tracked = build_example(conn, status="track")
    with conn:
        add_niche(conn, topic="p", status="proposed")
        add_niche(conn, topic="s", status="shelve")
    result = run_score(conn)
    assert [s.niche_id for s in result.scored] == [tracked]
    assert repo.get_niche(conn, tracked)["status"] == "track"


def test_longform_niche_is_flagged_longform_uncalibrated(conn: sqlite3.Connection) -> None:
    build_example(conn, fmt="longform")
    run_score(conn)
    (row,) = score_rows(conn)
    flags = json.loads(row["confidence_flags_json"])
    assert "longform_uncalibrated" in flags and "uncalibrated" not in flags
    # The example's videos are all 45 s Shorts: nothing in format, no small-channel views.
    assert "no_small_channels" not in flags and row["newcomer_monthly_views_p50"] == 0
    assert row["est_monthly_gbp"] == 0


def test_disqualified_niche_scores_zero_with_null_hours(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn)
    with conn:
        repo.set_niche_tags(
            conn,
            niche_id,
            required_steps=[*REQUIRED, "presenter"],
            needs_specific_footage=False,
            notes="",
            prompt_hash="p",
            schema_hash="s",
        )
    run_score(conn)
    (row,) = score_rows(conn)
    assert row["score"] == 0 and row["manual_hours_per_month"] is None
    assert "disqualified" in json.loads(row["confidence_flags_json"])


def test_specific_footage_raises_the_visuals_hours(conn: sqlite3.Connection) -> None:
    build_example(conn, footage=True)
    run_score(conn)
    (row,) = score_rows(conn)
    # visuals_stock partial 0.5 × 2.5/1.0 = 1.25 instead of 0.5 → 0.90 + 0.75 = 1.65 h/video.
    assert row["manual_hours_per_month"] == pytest.approx(1.65 * 20)


def test_specific_footage_pure() -> None:
    step = Step("visuals_stock", "V", 1.0, specific_footage_hours=2.5)
    s, cov = specific_footage(step, StepCoverage("visuals_stock", "partial", 0.5))
    assert (s.default_hours, cov.manual_hours_override) == (2.5, 1.25)
    s, cov = specific_footage(step, StepCoverage("visuals_stock", "manual"))
    assert (s.default_hours, cov.manual_hours_override) == (2.5, None)
    plain = Step("script", "S", 0.5)
    assert specific_footage(plain, StepCoverage("script", "partial", 0.1))[0] is plain
    hours, _ = manual_hours_per_video(["visuals_stock"], STEPS, COVERAGE, "shorts")
    assert hours == 0.5
    hours, _ = manual_hours_per_video(
        ["visuals_stock"], STEPS, COVERAGE, "shorts", needs_specific_footage=True
    )
    assert hours == 1.25


def test_ranked_table_lists_best_first(conn: sqlite3.Connection) -> None:
    build_example(conn)
    with conn:
        other = add_niche(conn, topic="other")
        repo.put_niche_channel(conn, other, "UCA", is_small=None)
    result = run_score(conn)
    table = scout_score.format_table(result)
    lines = table.splitlines()
    assert lines[0].split()[:6] == ["#", "id", "label", "format", "kept", "score"]
    assert "Top 5 countdown: dangerous-animals" in lines[2]
    assert "low_confidence" in lines[2]
    assert "Top 5 countdown: other" in lines[3]


# ---------------------------------------------------------------- the tagger


def test_schema_step_enum_matches_production_steps() -> None:
    schema = json.loads((REPO_ROOT / scout_tag.SCHEMA_RELPATH).read_text(encoding="utf-8"))
    enum = schema["properties"]["niches"]["items"]["properties"]["required_steps"]["items"]["enum"]
    assert enum == [s.id for s in load_steps(REPO_ROOT / "config" / "production_steps.yaml")]
    assert schema["properties"]["niches"]["maxItems"] == scout_tag.BATCH_SIZE


def test_prompt_names_the_disqualifying_steps() -> None:
    text = (REPO_ROOT / scout_tag.PROMPT_RELPATH).read_text(encoding="utf-8")
    assert "`footage_original` and `presenter` disqualify" in text


def test_tag_packet_carries_niche_fields_and_top_titles(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn, tagged=False)
    niche = repo.get_niche(conn, niche_id)
    packet = scout_tag.tag_packet(conn, [niche], STEPS, shorts_max_seconds=180)
    (n,) = packet["niches"]
    assert n["niche_id"] == niche_id and n["format"] == "shorts"
    assert n["queries"] == ["q1", "q2", "q3"] and n["why_ai_able"] == "stock and TTS"
    assert n["sample_titles"] == [
        "D animals video 0 (600000 views)",
        "C animals video 0 (90000 views)",
        "F animals video 0 (70000 views)",
        "D animals video 1 (60000 views)",
        "D animals video 2 (60000 views)",
    ]
    assert [s["id"] for s in packet["production_steps"]] == [s.id for s in STEPS]


def canned(answers: list[dict]) -> str:
    return json.dumps(
        {"type": "result", "is_error": False, "structured_output": {"niches": answers}}
    )


def add_validated(conn: sqlite3.Connection, n: int) -> list[int]:
    with conn:
        return [add_niche(conn, topic=f"t{i}", tagged=False) for i in range(n)]


def test_tag_niches_writes_tags_in_batches_of_ten(
    conn: sqlite3.Connection, fake_claude, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ids = add_validated(conn, 12)
    answers = [
        {"niche_id": i, "required_steps": ["research", "qa", "qa"], "needs_specific_footage": True,
         "notes": "x"}
        for i in ids[:5] + ids[10:]
    ]  # fmt: skip
    # The fake answers every call the same: batch 1 (ids 0-9) gets answers for 0-4, batch 2
    # (ids 10-11) for both. Answers about niches outside the batch are ignored.
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned(answers))
    result = scout_tag.tag_niches(
        conn, repo_root=REPO_ROOT, packets_dir=tmp_path / "p", shorts_max_seconds=180
    )
    assert result.failure is None
    assert result.calls == 2 and result.candidates == 12
    assert result.tagged == ids[:5] + ids[10:] and result.missing == ids[5:10]
    row = repo.get_niche(conn, ids[0])
    assert json.loads(row["required_steps_json"]) == ["research", "qa"]
    assert row["needs_specific_footage"] == 1
    assert row["tag_prompt_hash"] == result.prompt_hash
    # Only the unanswered niches are left to tag under the current prompt ...
    assert [r["id"] for r in repo.niches_to_tag(conn, result.prompt_hash, 50)] == ids[5:10]
    # ... and a prompt change re-opens them all.
    assert len(repo.niches_to_tag(conn, "other-hash", 50)) == 12


def test_tag_niches_leaves_a_missing_answer_untagged(
    conn: sqlite3.Connection, fake_claude, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ids = add_validated(conn, 2)
    answer = {"niche_id": ids[0], "required_steps": ["qa"], "needs_specific_footage": False}
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned([{**answer, "notes": ""}]))
    result = scout_tag.tag_niches(
        conn, repo_root=REPO_ROOT, packets_dir=tmp_path / "p", shorts_max_seconds=180
    )
    assert result.tagged == [ids[0]] and result.missing == [ids[1]]
    assert repo.get_niche(conn, ids[1])["tag_prompt_hash"] is None


def test_tag_niches_reports_a_failed_call(
    conn: sqlite3.Connection, fake_claude, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    add_validated(conn, 1)
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    result = scout_tag.tag_niches(
        conn, repo_root=REPO_ROOT, packets_dir=tmp_path / "p", shorts_max_seconds=180
    )
    assert result.failure and result.tagged == []


# ---------------------------------------------------------------- CLI


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp repo whose rpm_tiers and pipeline_coverage are the worked example's."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(f"own_channel_id: {OWN}\n", encoding="utf-8")
    for name in ("scoring.yaml", "production_steps.yaml"):
        shutil.copy(REPO_ROOT / "config" / name, config / name)
    rpm = yaml.safe_load((REPO_ROOT / "config" / "rpm_tiers.yaml").read_text(encoding="utf-8"))
    rpm["categories"]["animals_nature"]["shorts"]["usd_rpm"] = {"low": 0.02, "mid": 0.08, "high": 1}
    (config / "rpm_tiers.yaml").write_text(yaml.safe_dump(rpm), encoding="utf-8")
    coverage = {
        "audited_at": "2026-09-01",
        "pipeline_commit": "abc1234",
        "formats_supported": ["shorts", "longform"],
        "steps": {
            sid: {"coverage": c.coverage}
            | (
                {"manual_hours_override": c.manual_hours_override}
                if c.manual_hours_override
                else {}
            )
            for sid, c in COVERAGE.steps.items()
        },
    }
    (config / "pipeline_coverage.yaml").write_text(yaml.safe_dump(coverage), encoding="utf-8")
    for sub in ("prompts", "schemas"):
        shutil.copytree(REPO_ROOT / sub, tmp_path / sub)
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)
    return tmp_path


def db_path(root: Path) -> Path:
    return root / "data" / "ytscout.sqlite"


def test_cli_score_niches_writes_the_worked_example(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = connect(db_path(repo_root))
    build_example(c)
    with c:
        add_niche(c, topic="untagged", tagged=False)
    c.close()
    assert main(["score", "--niches"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Top 5 countdown: dangerous-animals" in out and "low_confidence" in out
    assert "1 niche_scores row(s) appended" in out
    assert "1 untagged niche(s) skipped" in out
    c = sqlite3.connect(db_path(repo_root))
    c.row_factory = sqlite3.Row
    try:
        (row,) = c.execute("SELECT * FROM niche_scores").fetchall()
        assert_worked_example(row)
    finally:
        c.close()


def test_cli_score_default_runs_competitors_and_niches(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = connect(db_path(repo_root))
    build_example(c)
    c.close()
    assert main(["score"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "score --competitors:" in out and "score --niches: 1 niche_scores row(s)" in out


@pytest.mark.parametrize("create", [False, True])
def test_cli_score_on_an_empty_db_has_nothing_to_score(
    repo_root: Path, capsys: pytest.CaptureFixture[str], create: bool
) -> None:
    if create:
        connect(db_path(repo_root)).close()
    assert main(["score"]) == EXIT_OK
    assert "nothing to score" in capsys.readouterr().out


def test_cli_scout_tag_dry_run_plans_and_writes_nothing(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = connect(db_path(repo_root))
    add_validated(c, 11)
    c.close()
    before = db_path(repo_root).read_bytes()
    assert main(["scout", "tag", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "planned: 11 niche(s) in 2 call(s)" in out
    assert db_path(repo_root).read_bytes() == before


def test_cli_scout_tag_tags_then_has_nothing_left(
    repo_root: Path, fake_claude, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    c = connect(db_path(repo_root))
    (nid,) = add_validated(c, 1)
    c.close()
    answer = {"niche_id": nid, "required_steps": REQUIRED, "needs_specific_footage": False}
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned([{**answer, "notes": "ok"}]))
    assert main(["scout", "tag"]) == EXIT_OK
    assert "1 of 1 niche(s) tagged in 1 call(s)" in capsys.readouterr().out
    assert main(["scout", "tag"]) == EXIT_OK
    assert "nothing to tag" in capsys.readouterr().out


def test_cli_scout_tag_exits_5_when_claude_fails(
    repo_root: Path, fake_claude, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    c = connect(db_path(repo_root))
    add_validated(c, 1)
    c.close()
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    assert main(["scout", "tag"]) == EXIT_CLAUDE_UNAVAILABLE
    assert "claude failed" in capsys.readouterr().err
