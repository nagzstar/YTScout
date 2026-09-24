"""Pipeline audit: the two YAML files load and match, the hours arithmetic is right, and
``ytscout audit`` exits 1 when the files disagree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ytscout.audit import (
    COVERAGE_RELPATH,
    STEPS_RELPATH,
    AuditError,
    PipelineCoverage,
    Step,
    StepCoverage,
    audit,
    effective_hours,
    format_table,
    load_coverage,
    load_steps,
    run_audit,
)
from ytscout.cli import EXIT_ERROR, EXIT_OK, main

REPO_ROOT = Path(__file__).resolve().parents[1]
STEPS_PATH = REPO_ROOT / STEPS_RELPATH
COVERAGE_PATH = REPO_ROOT / COVERAGE_RELPATH

# The twelve DESIGN.md §6.4 rows, in order.
DESIGN_STEP_IDS = [
    "research",
    "script",
    "voiceover",
    "visuals_stock",
    "footage_original",
    "presenter",
    "assembly",
    "thumbnail",
    "metadata",
    "upload",
    "qa",
    "community",
]


def run_cli(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ytscout", *argv],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


# ------------------------------------------------------------ the real files


def test_production_steps_loads_with_the_twelve_design_rows() -> None:
    steps = load_steps(STEPS_PATH)
    assert [s.id for s in steps] == DESIGN_STEP_IDS
    by_id = {s.id: s for s in steps}
    assert by_id["visuals_stock"].specific_footage_hours == 2.5
    assert by_id["footage_original"].disqualifying_for_faceless
    assert by_id["presenter"].disqualifying_for_faceless
    assert by_id["thumbnail"].shorts_hours == 0.0
    assert by_id["qa"].floor_hours == 0.1
    assert sum(s.default_hours for s in steps) == pytest.approx(11.25)


def test_pipeline_coverage_loads_with_metadata() -> None:
    cov = load_coverage(COVERAGE_PATH)
    assert cov.audited_at
    assert cov.pipeline_commit
    assert "shorts" in cov.formats_supported
    for entry in cov.steps.values():
        assert entry.notes, f"{entry.step_id}: every coverage call needs a note"
        if entry.coverage != "manual":
            assert entry.evidence, f"{entry.step_id}: automated/partial needs evidence"


def test_step_ids_match_one_to_one() -> None:
    steps = load_steps(STEPS_PATH)
    cov = load_coverage(COVERAGE_PATH)
    assert set(cov.steps) == {s.id for s in steps}
    assert len(cov.steps) == len(steps) == 12


def test_real_audit_runs_and_totals_are_sane() -> None:
    report = run_audit(STEPS_PATH, COVERAGE_PATH)
    assert len(report.rows) == 12
    for fmt in ("shorts", "longform"):
        assert 0.1 <= report.totals[fmt] <= 11.25
    # QA floor: a human always looks before publish.
    qa = next(r for r in report.rows if r.step.id == "qa")
    assert qa.hours["shorts"] >= 0.1
    # Shorts never pay for a thumbnail, and long-form costs at least as much as a Short.
    thumb = next(r for r in report.rows if r.step.id == "thumbnail")
    assert thumb.hours["shorts"] == 0.0
    assert report.totals["longform"] >= report.totals["shorts"]


# ------------------------------------------------------------ hand-worked arithmetic


def _step(step_id: str, hours: float, **kw: object) -> Step:
    return Step(id=step_id, label=step_id, default_hours=hours, **kw)  # type: ignore[arg-type]


def _cov(step_id: str, coverage: str, override: float | None = None) -> StepCoverage:
    return StepCoverage(step_id=step_id, coverage=coverage, manual_hours_override=override)  # type: ignore[arg-type]


HAND_STEPS = [
    _step("research", 0.75),
    _step("voiceover", 0.25),
    _step("visuals_stock", 1.0, specific_footage_hours=2.5),
    _step("presenter", 4.0, disqualifying_for_faceless=True),
    _step("thumbnail", 0.25, shorts_hours=0.0),
    _step("qa", 0.15, floor_hours=0.1),
]
HAND_COVERAGE = {
    "research": _cov("research", "manual"),  # 0.75 both formats
    "voiceover": _cov("voiceover", "automated"),  # 0
    "visuals_stock": _cov("visuals_stock", "partial", 0.4),  # 0.4
    "presenter": _cov("presenter", "manual"),  # 4.0 but disqualifying: excluded
    "thumbnail": _cov("thumbnail", "partial", 0.1),  # shorts 0 (capped), longform 0.1
    "qa": _cov("qa", "automated"),  # floor 0.1
}


def _pipeline(formats: tuple[str, ...] = ("shorts", "longform")) -> PipelineCoverage:
    return PipelineCoverage(
        audited_at="2026-09-24",
        pipeline_commit="abc1234",
        formats_supported=formats,  # type: ignore[arg-type]
        steps=dict(HAND_COVERAGE),
    )


def test_effective_hours_hand_worked() -> None:
    report = audit(HAND_STEPS, _pipeline())
    hours = {r.step.id: r.hours for r in report.rows}
    assert hours["research"] == {"shorts": 0.75, "longform": 0.75}
    assert hours["voiceover"] == {"shorts": 0.0, "longform": 0.0}
    assert hours["visuals_stock"] == {"shorts": 0.4, "longform": 0.4}
    assert hours["presenter"] == {"shorts": 4.0, "longform": 4.0}
    assert hours["thumbnail"] == {"shorts": 0.0, "longform": 0.1}
    assert hours["qa"] == {"shorts": 0.1, "longform": 0.1}
    # 0.75 + 0 + 0.4 + (presenter excluded) + thumbnail + 0.1
    assert report.totals["shorts"] == pytest.approx(1.25)
    assert report.totals["longform"] == pytest.approx(1.35)


def test_unsupported_format_is_costed_fully_manual() -> None:
    report = audit(HAND_STEPS, _pipeline(formats=("shorts",)))
    # longform: 0.75 + 0.25 + 1.0 + 0.25 + 0.15 = 2.4 (presenter excluded)
    assert report.totals["longform"] == pytest.approx(2.4)
    assert report.totals["shorts"] == pytest.approx(1.25)


def test_effective_hours_floor_beats_automated() -> None:
    qa = _step("qa", 0.15, floor_hours=0.1)
    assert effective_hours(qa, _cov("qa", "automated"), "shorts", supported=True) == 0.1
    assert effective_hours(qa, _cov("qa", "manual"), "longform", supported=True) == 0.15


def test_audit_rejects_missing_and_unknown_steps() -> None:
    cov = _pipeline()
    without = PipelineCoverage(
        audited_at=cov.audited_at,
        pipeline_commit=cov.pipeline_commit,
        formats_supported=cov.formats_supported,
        steps={k: v for k, v in cov.steps.items() if k != "qa"},
    )
    with pytest.raises(AuditError, match="without a coverage entry: qa"):
        audit(HAND_STEPS, without)
    extra = PipelineCoverage(
        audited_at=cov.audited_at,
        pipeline_commit=cov.pipeline_commit,
        formats_supported=cov.formats_supported,
        steps={**cov.steps, "juggling": _cov("juggling", "manual")},
    )
    with pytest.raises(AuditError, match="unknown steps: juggling"):
        audit(HAND_STEPS, extra)


def test_audit_rejects_override_above_default() -> None:
    cov = _pipeline()
    too_big = PipelineCoverage(
        audited_at=cov.audited_at,
        pipeline_commit=cov.pipeline_commit,
        formats_supported=cov.formats_supported,
        steps={**cov.steps, "research": _cov("research", "partial", 0.9)},
    )
    with pytest.raises(AuditError, match="exceeds default_hours"):
        audit(HAND_STEPS, too_big)


def test_load_coverage_validates_shape(tmp_path: Path) -> None:
    bad = tmp_path / "coverage.yaml"
    bad.write_text(
        "audited_at: 2026-09-24\npipeline_commit: abc\nformats_supported: [shorts]\n"
        "steps:\n  qa: {coverage: partial}\n",
        encoding="utf-8",
    )
    with pytest.raises(AuditError, match="partial coverage needs manual_hours_override"):
        load_coverage(bad)
    bad.write_text(
        "audited_at: 2026-09-24\npipeline_commit: abc\nformats_supported: [vhs]\nsteps: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(AuditError, match="unknown format"):
        load_coverage(bad)
    with pytest.raises(AuditError, match="file not found"):
        load_steps(tmp_path / "nope.yaml")


def test_format_table_lists_every_step_and_both_totals() -> None:
    text = format_table(audit(HAND_STEPS, _pipeline()))
    for step in HAND_STEPS:
        assert step.id in text
    assert "presenter *" in text
    assert "manual_hours_per_video (short):    1.25" in text
    assert "manual_hours_per_video (longform): 1.35" in text


# ------------------------------------------------------------ the CLI


def test_cli_audit_exits_0_and_prints_the_table() -> None:
    result = run_cli("audit", cwd=REPO_ROOT)
    assert result.returncode == EXIT_OK, result.stderr
    for step_id in DESIGN_STEP_IDS:
        assert step_id in result.stdout
    assert "manual_hours_per_video (short):" in result.stdout
    assert "manual_hours_per_video (longform):" in result.stdout


def test_cli_audit_exits_1_when_a_step_is_missing(tmp_path: Path) -> None:
    doc = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8"))
    del doc["steps"]["upload"]
    broken = tmp_path / "pipeline_coverage.yaml"
    broken.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    result = run_cli("audit", "--coverage", str(broken), cwd=REPO_ROOT)
    assert result.returncode == EXIT_ERROR
    assert "without a coverage entry: upload" in result.stderr


def test_cli_audit_exits_1_when_steps_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["audit", "--steps", str(tmp_path / "missing.yaml")])
    assert code == EXIT_ERROR
    assert "file not found" in capsys.readouterr().err


def test_cli_audit_rejects_unknown_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "--dry-run"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
