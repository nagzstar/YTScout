"""``scout sensitivity`` (issue 028): the 27-cell grid over issue 023's worked example.

The example is rebuilt as DB rows by ``test_scout_score.build_example``. Under the default
cell the numbers are the 023 numbers; the other cells move the small set:

- ``small_subs_max=5000`` keeps only A small (B has exactly 5,000): the p75 anchor (051)
  drops from 26,667 to A's 13,333, halving the score, and the opportunity rises (outlier
  rate 1/1).
- ``small_age_days=180`` leaves no small channel (A is 184 days old): views None, £0,
  score 0, flag ``no_small_channels``.
- ``outlier_multiplier`` never changes the score (opportunity is not in it) and, in the
  example, not even the outlier set (A's 30,000 clears 4 × 2,000; C's 90,000 clears 4 × 6,000).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_scoring import CFG, COVERAGE, NOW, STEPS, TABLE, sig3
from test_scout_score import add_niche, assert_worked_example, build_example, db_path
from test_scout_score import repo_root as _repo_root

from ytscout.cli import EXIT_ERROR, EXIT_OK, main
from ytscout.scout import sensitivity
from ytscout.store import connect, repo

repo_root = _repo_root  # the CLI fixture from test_scout_score, re-exported for pytest

DEFAULT = (3, 10000, 365)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def run(conn: sqlite3.Connection, niche_ids=None) -> sensitivity.SensitivityResult:
    niches, missing = sensitivity.target_niches(conn, niche_ids)
    assert missing == []
    return sensitivity.run_grid(
        conn, niches, cfg=CFG, rpm=TABLE, steps=STEPS, coverage=COVERAGE, usd_gbp=0.78, now=NOW
    )


# ---------------------------------------------------------------- the grid


def test_grid_has_27_settings_and_the_default_is_the_yaml_default() -> None:
    grid = sensitivity.settings_grid()
    assert len(grid) == 27 and len(set(grid)) == 27
    assert sensitivity.default_setting(CFG) == DEFAULT
    assert DEFAULT in grid
    # The middle value of every axis is the default, so the grid brackets it both ways.
    for axis, values in sensitivity.GRID.items():
        assert values[1] == CFG[axis]


def test_patched_overrides_only_the_axes() -> None:
    cfg = sensitivity.patched(CFG, (2, 5000, 180))
    assert (cfg["outlier_multiplier"], cfg["small_subs_max"], cfg["small_age_days"]) == (
        2,
        5000,
        180,
    )
    assert cfg["window_days"] == CFG["window_days"] and CFG["outlier_multiplier"] == 3


def test_default_cell_is_the_023_worked_example(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn, status="scored")
    result = run(conn)
    assert len(result.cells) == 27
    assert result.niche_ids == [niche_id]
    (score,) = result.default_cell.scores
    assert_worked_example(score.values)
    assert score.flags == ["low_confidence"]


def test_grid_moves_the_score_where_the_small_set_changes(conn: sqlite3.Connection) -> None:
    build_example(conn, status="scored")
    result = run(conn)
    (row,) = result.per_niche()
    assert sig3(row.score_default) == 0.116
    assert row.score_min == 0.0  # small_age_days=180: no small channel
    assert sig3(row.score_max) == 0.116
    assert sig3(row.opportunity_default) == 0.387
    # small_subs_max=5000 keeps only A: rate 1.0 → 0.5 + 0.3×40/1465 + 0.2×0.099 → capped 0.4.
    assert sig3(row.opportunity_max) == 0.4
    assert row.opportunity_min == pytest.approx(0.2 * (1 - 0.901), abs=1e-3)
    assert (row.rank_default, row.rank_min, row.rank_max, row.stable_settings) == (1, 1, 1, 27)
    assert row.score_min_setting == (2, 5000, 180) and row.score_spread is None
    assert row.score_max_setting == (2, 10000, 365)  # first cell in grid order at 0.116
    no_small = result.cell((3, 10000, 180)).scores[0]
    assert "no_small_channels" in no_small.flags and no_small.score == 0
    only_a = result.cell((3, 5000, 365)).scores[0]
    assert sig3(only_a.values["newcomer_monthly_views_p50"]) == 13_300
    assert sig3(only_a.score) == 0.0578  # anchor = A's 13,333 alone
    assert sig3(only_a.values["small_outlier_rate"]) == 1.0


def test_outlier_multiplier_never_moves_the_score(conn: sqlite3.Connection) -> None:
    build_example(conn, status="scored")
    result = run(conn)
    for subs in sensitivity.GRID["small_subs_max"]:
        for age in sensitivity.GRID["small_age_days"]:
            scores = {result.cell((m, subs, age)).scores[0].score for m in (2, 3, 4)}
            assert len(scores) == 1
    assert result.axis_effects()["outlier_multiplier"] == 0


def test_ranking_and_top_changes_with_two_niches(conn: sqlite3.Connection) -> None:
    first = build_example(conn, status="scored")
    with conn:
        # A second niche sampled on A alone: p50 = 13,333 under every setting that keeps A
        # small, £0 when A is not small (age 180).
        second = add_niche(conn, topic="other", status="track")
        repo.put_niche_channel(conn, second, "UCA", is_small=None)
    result = run(conn)
    assert result.niche_ids == [first, second]
    by_id = {r.niche_id: r for r in result.per_niche()}
    # The first niche's p75 anchor is never below A's 13,333 (A only, or A, B, C) and the
    # hours are the same: at worst a tie, broken by id, so the ranks never move.
    assert by_id[first].rank_default == 1 and by_id[second].rank_default == 2
    assert by_id[first].stable_settings == 27 and by_id[second].stable_settings == 27
    assert result.most_disruptive() is None
    assert all(changed == 0 for _, changed in result.top_changes())
    assert result.default_cell.top() == [first, second]


def test_niche_ids_override_the_status_filter(conn: sqlite3.Connection) -> None:
    validated = build_example(conn)  # status validated: not in the default run
    assert run(conn).niche_ids == []
    assert run(conn, [validated]).niche_ids == [validated]
    _, missing = sensitivity.target_niches(conn, [validated, 999])
    assert missing == [999]


def test_untagged_niche_is_counted_not_scored(conn: sqlite3.Connection) -> None:
    build_example(conn, status="scored", tagged=False)
    result = run(conn)
    assert result.niche_ids == [] and result.skipped_untagged == 1


# ---------------------------------------------------------------- the report


def test_report_mentions_the_niche_and_the_thin_sample(conn: sqlite3.Connection) -> None:
    build_example(conn, status="scored")
    result = run(conn)
    text = sensitivity.format_report(result)
    assert "Top 5 countdown: dangerous-animals" in text
    assert "The sample is thin" in text and "1 niche(s)" in text
    assert "outlier_multiplier=3 small_subs_max=10000 small_age_days=365" in text
    assert "`outlier_multiplier` alone never changes the ranking" in text
    assert "Widest score swing: #" in text and "(from 0), lowest at" in text
    assert text.count("\n| ") >= 27 + 1  # every grid cell has a row
    summary = sensitivity.format_summary(result)
    assert "sample is thin" in summary and "27/27" in summary


# ---------------------------------------------------------------- CLI


def test_cli_scout_sensitivity_writes_the_report_and_nothing_else(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = connect(db_path(repo_root))
    build_example(c, status="scored")
    c.close()
    before = db_path(repo_root).read_bytes()
    assert main(["scout", "sensitivity"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Top 5 countdown: dangerous-animals" in out
    assert "0 API units spent" in out
    report = repo_root / "docs" / "sensitivity.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "Top 5 countdown: dangerous-animals" in text
    assert "| 0.116 |" in text  # the 023 example's score under the default cell (051: p75)
    assert db_path(repo_root).read_bytes() == before
    c = sqlite3.connect(db_path(repo_root))
    try:
        assert c.execute("SELECT COUNT(*) FROM niche_scores").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        c.close()


def test_cli_scout_sensitivity_niches_and_out(
    repo_root: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    c = connect(db_path(repo_root))
    nid = build_example(c)  # validated only: --niches must name it
    c.close()
    target = tmp_path / "elsewhere" / "s.md"
    assert main(["scout", "sensitivity", "--niches", str(nid), "--out", str(target)]) == EXIT_OK
    assert target.is_file() and "dangerous-animals" in capsys.readouterr().out
    assert main(["scout", "sensitivity", "--niches", "999"]) == EXIT_ERROR
    assert "no niche with id 999" in capsys.readouterr().err


def test_cli_scout_sensitivity_with_nothing_scored(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    connect(db_path(repo_root)).close()
    assert main(["scout", "sensitivity"]) == EXIT_OK
    assert "no scored or tracked niches" in capsys.readouterr().out
    assert not (repo_root / "docs" / "sensitivity.md").exists()


def test_cli_scout_sensitivity_without_a_db(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scout", "sensitivity"]) == EXIT_ERROR
    assert "no database" in capsys.readouterr().err
