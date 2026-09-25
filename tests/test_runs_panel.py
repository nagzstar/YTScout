"""031: ``runs`` rows record what they spent, and the dashboard's Runs section shows it."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_scout_score import db_path, repo_root  # noqa: F401

from ytscout import claude_runner
from ytscout.cli import EXIT_OK, RUN_LOG_ENV, main, recorded_run
from ytscout.dashboard import build
from ytscout.dashboard.build import load_runs, run_health, week_start
from ytscout.settings import find_repo_root
from ytscout.store import connect, read_copy, to_utc_iso
from ytscout.youtube import Ledger

REPO_ROOT = find_repo_root(Path(__file__).parent)
PROMPT = REPO_ROOT / "prompts" / "video_summary.md"
SCHEMA = REPO_ROOT / "schemas" / "video_summary.json"

# A Friday 12:00 UTC: the week (from Monday 00:00 UTC) started 2030-01-07.
NOW = datetime(2030, 1, 11, 12, 0, tzinfo=UTC)
WEEKLY_LOG = r"C:\repo\logs\weekly-20300107-0300.log"
OLD_WEEKLY_LOG = r"C:\repo\logs\weekly-20291231-0300.log"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def add_run(
    conn: sqlite3.Connection,
    kind: str,
    started: datetime,
    status: str | None = "ok",
    *,
    log_path: str | None = WEEKLY_LOG,
    minutes: float = 2,
    units: int | None = 0,
    calls: int | None = 0,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost: float | None = None,
    error_tail: str | None = None,
) -> None:
    finished = None if status is None else to_utc_iso(started + timedelta(minutes=minutes))
    with conn:
        conn.execute(
            "INSERT INTO runs (started_at, finished_at, kind, status, log_path, units_used,"
            " claude_calls, claude_input_tokens, claude_output_tokens, claude_cost_usd_est,"
            " error_tail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                to_utc_iso(started),
                finished,
                kind,
                status,
                log_path,
                units,
                calls,
                tokens_in,
                tokens_out,
                cost,
                error_tail,
            ),
        )


def rows(conn: sqlite3.Connection) -> list[dict]:
    """All runs, newest first, as ``load_runs`` reads them."""
    return load_runs(conn, NOW)["recent"]


def all_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs ORDER BY id").fetchall()


def render(conn: sqlite3.Connection, tmp_path: Path) -> str:
    out = tmp_path / "index.html"
    build(conn, out, now=NOW)
    return out.read_text(encoding="utf-8")


# --- the banner --------------------------------------------------------------------------


def test_a_recent_all_ok_weekly_run_shows_no_banner(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    monday = datetime(2030, 1, 7, 3, 0, tzinfo=UTC)
    add_run(conn, "collect_own", monday)
    add_run(conn, "score_niches", monday + timedelta(minutes=30))
    # An older weekly run that failed no longer matters.
    add_run(conn, "collect_own", monday - timedelta(days=7), "error", log_path=OLD_WEEKLY_LOG)
    assert run_health(rows(conn), NOW) == []
    html = render(conn, tmp_path)
    assert 'id="runs-banner"' not in html
    assert 'id="runs"' in html


def test_an_error_step_in_the_newest_weekly_run_shows_the_banner(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    monday = datetime(2030, 1, 7, 3, 0, tzinfo=UTC)
    add_run(conn, "collect_own", monday)
    add_run(conn, "analyse_competitors", monday + timedelta(minutes=10), "error")
    add_run(conn, "score_niches", monday + timedelta(minutes=30))
    # A later manual run that went fine does not hide the weekly failure.
    add_run(conn, "scout_tag", NOW - timedelta(hours=1), log_path=None)
    reasons = run_health(rows(conn), NOW)
    assert reasons == ["the newest weekly run did not finish ok: analyse_competitors error"]
    html = render(conn, tmp_path)
    assert 'id="runs-banner"' in html
    assert "analyse_competitors error" in html


def test_a_stale_weekly_run_shows_the_banner(conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_run(conn, "collect_own", NOW - timedelta(days=8, minutes=1))
    assert run_health(rows(conn), NOW) == ["no run in the last 8 days"]
    assert 'id="runs-banner"' in render(conn, tmp_path)


def test_eight_days_exactly_is_not_stale(conn: sqlite3.Connection) -> None:
    add_run(conn, "collect_own", NOW - timedelta(days=8))
    assert run_health(rows(conn), NOW) == []


def test_without_weekly_rows_the_newest_run_stands_in(conn: sqlite3.Connection) -> None:
    add_run(conn, "score_niches", NOW - timedelta(days=2), log_path=None)
    add_run(conn, "collect_own", NOW - timedelta(days=1), "quota_exhausted", log_path=None)
    assert run_health(rows(conn), NOW) == [
        "the newest run did not finish ok: collect_own quota_exhausted"
    ]


def test_no_runs_at_all_shows_the_banner(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert run_health([], NOW) == ["no run in the last 8 days"]
    html = render(conn, tmp_path)
    assert 'id="runs-banner"' in html
    assert "Nothing yet." in html


# --- totals, chart, table ----------------------------------------------------------------


def test_week_totals_sum_this_weeks_runs_only(conn: sqlite3.Connection) -> None:
    assert week_start(NOW) == datetime(2030, 1, 7, tzinfo=UTC)
    add_run(conn, "collect_own", datetime(2030, 1, 6, 23, 59, tzinfo=UTC), units=5000)  # Sunday
    add_run(conn, "collect_own", datetime(2030, 1, 7, 3, 0, tzinfo=UTC), units=120)
    add_run(
        conn,
        "analyse_summaries",
        datetime(2030, 1, 7, 3, 5, tzinfo=UTC),
        units=0,
        calls=3,
        tokens_in=1000,
        tokens_out=200,
        cost=0.25,
    )
    add_run(
        conn,
        "analyse_competitors",
        datetime(2030, 1, 8, 3, 5, tzinfo=UTC),
        "error",
        units=None,
        calls=1,
        tokens_in=None,
        tokens_out=50,
        cost=None,
    )
    add_run(conn, "scout_validate", datetime(2030, 1, 9, 3, 5, tzinfo=UTC), units=30)
    week = load_runs(conn, NOW)["week"]
    assert week["runs"] == 4
    assert week["not_ok"] == 1
    assert week["units_used"] == 150  # 120 + 0 + 30; Sunday's 5,000 is last week
    assert week["claude_calls"] == 4
    assert week["claude_input_tokens"] == 1000
    assert week["claude_output_tokens"] == 250
    assert week["claude_cost_usd_est"] == pytest.approx(0.25)


def test_week_token_totals_are_null_when_nothing_reported(conn: sqlite3.Connection) -> None:
    add_run(conn, "score_niches", NOW - timedelta(hours=2))
    week = load_runs(conn, NOW)["week"]
    assert week["claude_input_tokens"] is None and week["claude_cost_usd_est"] is None
    assert week["claude_calls"] == 0


def test_units_chart_covers_fourteen_days_zero_filled(conn: sqlite3.Connection) -> None:
    # NOW is 04:00 Pacific on 2030-01-11.
    with conn:
        for day, units in [
            ("2029-12-28", 999),  # 15 days back: outside the chart
            ("2029-12-29", 10),
            ("2030-01-07", 8000),
            ("2030-01-11", 42),
        ]:
            conn.execute(
                "INSERT INTO quota_ledger (day_pacific, units_used) VALUES (?, ?)", (day, units)
            )
    chart = load_runs(conn, NOW)["units_by_day"]
    assert len(chart["labels"]) == 14 == len(chart["units"])
    assert chart["labels"][0] == "2029-12-29" and chart["labels"][-1] == "2030-01-11"
    assert chart["units"][0] == 10 and chart["units"][-1] == 42
    assert chart["units"][chart["labels"].index("2030-01-07")] == 8000
    assert sum(chart["units"]) == 8052


def test_recent_shows_ten_runs_with_durations_and_the_latest_log(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for n in range(12):
        add_run(conn, f"kind{n}", NOW - timedelta(hours=12 - n), log_path=None, minutes=1.5)
    add_run(conn, "collect_own", NOW - timedelta(days=20), tokens_in=7, tokens_out=3)
    panel = load_runs(conn, NOW)
    assert [r["kind"] for r in panel["recent"]] == [f"kind{n}" for n in range(11, 1, -1)]
    assert panel["recent"][0]["duration_s"] == pytest.approx(90)
    assert panel["log_path"] == WEEKLY_LOG
    html = render(conn, tmp_path)
    assert f'<code id="run-log">{WEEKLY_LOG}</code>' in html
    assert 'href="' + WEEKLY_LOG not in html  # text, not a link
    assert 'id="units-chart"' in html
    data = json.loads(html.split('id="units-data">', 1)[1].split("</script>", 1)[0])
    assert len(data["labels"]) == 14
    assert html.count("<td>kind") == 10
    assert "1:30" in html


def test_pending_analyses_count_since_the_last_ok(conn: sqlite3.Connection) -> None:
    with conn:
        for status in ("pending", "ok", "pending", "pending"):
            conn.execute(
                "INSERT INTO competitor_analyses (run_at, prompt_hash, status)"
                " VALUES ('2030-01-01T00:00:00Z', 'h', ?)",
                (status,),
            )
    assert load_runs(conn, NOW)["pending"] == 2


def test_error_tail_is_shown_under_the_run(conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_run(conn, "collect_own", NOW - timedelta(hours=1), "error", error_tail="Boom <b>")
    html = render(conn, tmp_path)
    assert "Boom &lt;b&gt;" in html
    assert 'class="status-bad"' in html


# --- recorded_run ------------------------------------------------------------------------


def test_recorded_run_stores_ledger_units_and_the_log_path(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RUN_LOG_ENV, WEEKLY_LOG)
    ledger = Ledger(conn, daily_cap=9000)
    ledger.charge(5)
    with recorded_run(conn, "collect_own"):
        ledger.charge(3)
        ledger.charge(100)
    (row,) = all_runs(conn)
    assert (row["status"], row["units_used"], row["claude_calls"]) == ("ok", 103, 0)
    assert row["log_path"] == WEEKLY_LOG
    assert row["claude_input_tokens"] is None and row["error_tail"] is None


def test_recorded_run_keeps_the_traceback_tail_and_reraises(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(RUN_LOG_ENV, raising=False)
    with pytest.raises(RuntimeError), recorded_run(conn, "score_niches"):
        raise RuntimeError("x" * 600 + " the end")
    (row,) = all_runs(conn)
    assert row["status"] == "error" and row["log_path"] is None
    assert len(row["error_tail"]) == 500
    assert row["error_tail"].rstrip().endswith("the end")
    assert row["units_used"] == 0


def test_recorded_run_counts_claude_usage(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path
) -> None:
    packet = tmp_path / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    claude_runner.run(PROMPT, packet, SCHEMA)  # before the run: not counted
    with recorded_run(conn, "analyse_summaries"):
        claude_runner.run(PROMPT, packet, SCHEMA)
        claude_runner.run(PROMPT, packet, SCHEMA)
    (row,) = all_runs(conn)
    # The fake reports usage {input_tokens: 100, output_tokens: 50}, cost 0.0123 per call.
    assert row["claude_calls"] == 2
    assert (row["claude_input_tokens"], row["claude_output_tokens"]) == (200, 100)
    assert row["claude_cost_usd_est"] == pytest.approx(0.0246)


def test_missing_usage_fields_are_stored_null(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = tmp_path / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    output = {"type": "result", "is_error": False, "structured_output": {}}
    schema = tmp_path / "s.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", json.dumps(output))
    with recorded_run(conn, "analyse_summaries"):
        claude_runner.run(PROMPT, packet, schema)
    (row,) = all_runs(conn)
    assert row["claude_calls"] == 1
    assert row["claude_input_tokens"] is None and row["claude_output_tokens"] is None
    assert row["claude_cost_usd_est"] is None


def test_a_failed_claude_call_is_still_counted(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = tmp_path / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    with recorded_run(conn, "analyse_summaries") as run:
        with pytest.raises(claude_runner.ClaudeUnavailable):
            claude_runner.run(PROMPT, packet, SCHEMA)
        run.status = "error"
    (row,) = all_runs(conn)
    assert (row["status"], row["claude_calls"]) == ("error", 1)


def test_input_tokens_include_cache_tokens() -> None:
    usage = claude_runner.Usage()
    usage.record(
        {
            "input_tokens": 10,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 3000,
            "output_tokens": 7,
        },
        0.5,
    )
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd_est) == (3210, 7, 0.5)


# --- the CLI -----------------------------------------------------------------------------


def test_cli_score_adds_a_runs_row_with_units_used(
    repo_root: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    connect(db_path(repo_root)).close()
    assert main(["score"]) == EXIT_OK
    capsys.readouterr()
    c = read_copy(db_path(repo_root))
    try:
        found = c.execute("SELECT kind, status, units_used, claude_calls FROM runs").fetchall()
    finally:
        c.close()
    assert {r["kind"] for r in found} == {"score_competitors", "score_niches"}
    for r in found:
        assert (r["status"], r["units_used"], r["claude_calls"]) == ("ok", 0, 0)
