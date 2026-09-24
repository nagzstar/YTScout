"""Pipeline audit: production steps × what ``top-five-animals-1`` automates.

Two YAML files, both pure data:

* ``config/production_steps.yaml`` - the DESIGN.md §6.4 step list with default manual hours.
* ``config/pipeline_coverage.yaml`` - per step, whether the pipeline does it
  (``automated``), half-does it (``partial``, with the hours a human still spends) or does
  not do it (``manual``). Written by issue 019, corrected by Nagz in 020.

``effective_hours`` is the arithmetic the M4 scoring (issue 023) reuses: the hours a human
still spends on one step, for one format, given the coverage. No DB, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Format = Literal["shorts", "longform"]
Coverage = Literal["automated", "partial", "manual"]

FORMATS: tuple[Format, ...] = ("shorts", "longform")
COVERAGES: tuple[Coverage, ...] = ("automated", "partial", "manual")

STEPS_RELPATH = Path("config") / "production_steps.yaml"
COVERAGE_RELPATH = Path("config") / "pipeline_coverage.yaml"


class AuditError(Exception):
    """A YAML file is missing, malformed, or the two files disagree."""


@dataclass(frozen=True)
class Step:
    id: str
    label: str
    default_hours: float
    notes: str = ""
    specific_footage_hours: float | None = None
    disqualifying_for_faceless: bool = False
    shorts_hours: float | None = None
    floor_hours: float = 0.0

    def base_hours(self, fmt: Format) -> float:
        """Hours a human spends on this step, for this format, with no pipeline at all."""
        if fmt == "shorts" and self.shorts_hours is not None:
            return self.shorts_hours
        return self.default_hours


@dataclass(frozen=True)
class StepCoverage:
    step_id: str
    coverage: Coverage
    manual_hours_override: float | None = None
    evidence: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class PipelineCoverage:
    audited_at: str
    pipeline_commit: str
    formats_supported: tuple[Format, ...]
    steps: dict[str, StepCoverage] = field(default_factory=dict)
    pipeline_repo: str = ""


@dataclass(frozen=True)
class AuditRow:
    step: Step
    coverage: StepCoverage
    hours: dict[Format, float]


@dataclass(frozen=True)
class AuditReport:
    rows: tuple[AuditRow, ...]
    totals: dict[Format, float]
    coverage: PipelineCoverage


# ---------------------------------------------------------------- loading


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuditError(f"file not found: {path}")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AuditError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise AuditError(f"{path}: top level must be a mapping")
    return doc


def _hours(where: str, key: str, value: Any, *, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise AuditError(f"{where}: {key} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AuditError(f"{where}: {key} must be a number, got {value!r}")
    if value < 0:
        raise AuditError(f"{where}: {key} must not be negative, got {value!r}")
    return float(value)


def load_steps(path: Path) -> list[Step]:
    """Load and validate ``production_steps.yaml``. Ids are unique and non-empty."""
    doc = _read_yaml(path)
    raw_steps = doc.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise AuditError(f"{path}: 'steps' must be a non-empty list")
    steps: list[Step] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_steps):
        where = f"{path}: steps[{index}]"
        if not isinstance(raw, dict):
            raise AuditError(f"{where}: must be a mapping")
        step_id = raw.get("id")
        if not isinstance(step_id, str) or not step_id:
            raise AuditError(f"{where}: id is required")
        if step_id in seen:
            raise AuditError(f"{where}: duplicate id {step_id!r}")
        seen.add(step_id)
        where = f"{path}: step {step_id!r}"
        label = raw.get("label")
        if not isinstance(label, str) or not label:
            raise AuditError(f"{where}: label is required")
        default_hours = _hours(where, "default_hours", raw.get("default_hours"), required=True)
        assert default_hours is not None
        steps.append(
            Step(
                id=step_id,
                label=label,
                default_hours=default_hours,
                notes=str(raw.get("notes") or ""),
                specific_footage_hours=_hours(
                    where, "specific_footage_hours", raw.get("specific_footage_hours")
                ),
                disqualifying_for_faceless=bool(raw.get("disqualifying_for_faceless", False)),
                shorts_hours=_hours(where, "shorts_hours", raw.get("shorts_hours")),
                floor_hours=_hours(where, "floor_hours", raw.get("floor_hours")) or 0.0,
            )
        )
    return steps


def load_coverage(path: Path) -> PipelineCoverage:
    """Load and validate ``pipeline_coverage.yaml`` on its own (ids are checked in ``audit``)."""
    doc = _read_yaml(path)
    for key in ("audited_at", "pipeline_commit"):
        if not doc.get(key):
            raise AuditError(f"{path}: {key} is required")
    raw_formats = doc.get("formats_supported")
    if not isinstance(raw_formats, list) or not raw_formats:
        raise AuditError(f"{path}: formats_supported must be a non-empty list")
    for fmt in raw_formats:
        if fmt not in FORMATS:
            raise AuditError(f"{path}: unknown format {fmt!r} (expected one of {FORMATS})")
    raw_steps = doc.get("steps")
    if not isinstance(raw_steps, dict) or not raw_steps:
        raise AuditError(f"{path}: 'steps' must be a non-empty mapping keyed by step id")
    steps: dict[str, StepCoverage] = {}
    for step_id, raw in raw_steps.items():
        where = f"{path}: step {step_id!r}"
        if not isinstance(raw, dict):
            raise AuditError(f"{where}: must be a mapping")
        coverage = raw.get("coverage")
        if coverage not in COVERAGES:
            raise AuditError(f"{where}: coverage must be one of {COVERAGES}, got {coverage!r}")
        override = _hours(where, "manual_hours_override", raw.get("manual_hours_override"))
        if coverage == "partial" and override is None:
            raise AuditError(f"{where}: partial coverage needs manual_hours_override")
        if coverage != "partial" and override is not None:
            raise AuditError(f"{where}: manual_hours_override only applies to partial coverage")
        evidence = raw.get("evidence") or []
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
            raise AuditError(f"{where}: evidence must be a list of paths")
        steps[str(step_id)] = StepCoverage(
            step_id=str(step_id),
            coverage=coverage,
            manual_hours_override=override,
            evidence=tuple(evidence),
            notes=str(raw.get("notes") or ""),
        )
    return PipelineCoverage(
        audited_at=str(doc["audited_at"]),
        pipeline_commit=str(doc["pipeline_commit"]),
        formats_supported=tuple(raw_formats),
        steps=steps,
        pipeline_repo=str(doc.get("pipeline_repo") or ""),
    )


# ---------------------------------------------------------------- arithmetic


def effective_hours(step: Step, coverage: StepCoverage, fmt: Format, *, supported: bool) -> float:
    """Hours a human still spends on ``step`` for one video of ``fmt``.

    * ``automated`` -> 0; ``manual`` -> the step's base hours for the format;
      ``partial`` -> the audited override, but never more than the base (a Short's
      thumbnail is 0 whatever the coverage says).
    * A format the pipeline cannot produce at all (``supported=False``) is costed as if
      every step were manual.
    * ``floor_hours`` applies last: QA never drops below 0.1 even when automated.
    """
    base = step.base_hours(fmt)
    kind: Coverage = coverage.coverage if supported else "manual"
    if kind == "automated":
        hours = 0.0
    elif kind == "manual":
        hours = base
    else:
        assert coverage.manual_hours_override is not None
        hours = min(coverage.manual_hours_override, base)
    return max(hours, step.floor_hours)


def audit(steps: list[Step], coverage: PipelineCoverage) -> AuditReport:
    """Cross-check the two files and compute per-step and total hours per format.

    Raises ``AuditError`` when a step has no coverage entry or a coverage entry names an
    unknown step, or when a partial override exceeds the step's default hours.
    """
    step_ids = [s.id for s in steps]
    missing = [s for s in step_ids if s not in coverage.steps]
    unknown = [s for s in coverage.steps if s not in step_ids]
    problems: list[str] = []
    if missing:
        problems.append(f"steps without a coverage entry: {', '.join(missing)}")
    if unknown:
        problems.append(f"coverage entries for unknown steps: {', '.join(unknown)}")
    for step in steps:
        cov = coverage.steps.get(step.id)
        if cov is None or cov.manual_hours_override is None:
            continue
        if cov.manual_hours_override > step.default_hours:
            problems.append(
                f"step {step.id!r}: manual_hours_override {cov.manual_hours_override} exceeds "
                f"default_hours {step.default_hours}"
            )
    if problems:
        raise AuditError("; ".join(problems))

    rows: list[AuditRow] = []
    totals: dict[Format, float] = dict.fromkeys(FORMATS, 0.0)
    for step in steps:
        cov = coverage.steps[step.id]
        hours: dict[Format, float] = {}
        for fmt in FORMATS:
            supported = fmt in coverage.formats_supported
            hours[fmt] = effective_hours(step, cov, fmt, supported=supported)
            if not step.disqualifying_for_faceless:
                totals[fmt] += hours[fmt]
        rows.append(AuditRow(step=step, coverage=cov, hours=hours))
    totals = {fmt: round(value, 4) for fmt, value in totals.items()}
    return AuditReport(rows=tuple(rows), totals=totals, coverage=coverage)


def run_audit(steps_path: Path, coverage_path: Path) -> AuditReport:
    return audit(load_steps(steps_path), load_coverage(coverage_path))


# ---------------------------------------------------------------- rendering


def format_table(report: AuditReport) -> str:
    """Plain-text table: step, default h, coverage, effective h per format, then totals."""
    header = ("step", "default h", "coverage", "shorts h", "longform h")
    body: list[tuple[str, ...]] = []
    for row in report.rows:
        step_col = row.step.id + (" *" if row.step.disqualifying_for_faceless else "")
        body.append(
            (
                step_col,
                f"{row.step.default_hours:.2f}",
                row.coverage.coverage,
                f"{row.hours['shorts']:.2f}",
                f"{row.hours['longform']:.2f}",
            )
        )
    widths = [max(len(r[i]) for r in (header, *body)) for i in range(len(header))]

    def line(cells: tuple[str, ...]) -> str:
        parts = [cells[0].ljust(widths[0])]
        parts += [cells[i].rjust(widths[i]) for i in range(1, len(cells))]
        return "  ".join(parts)

    lines = [line(header), line(tuple("-" * w for w in widths))]
    lines += [line(r) for r in body]
    cov = report.coverage
    lines.append("")
    lines.append(
        f"pipeline {cov.pipeline_commit} audited {cov.audited_at}; "
        f"formats supported: {', '.join(cov.formats_supported)}"
    )
    lines.append("* disqualifying for a faceless pipeline; excluded from the totals below")
    lines.append(f"manual_hours_per_video (short):    {report.totals['shorts']:.2f}")
    lines.append(f"manual_hours_per_video (longform): {report.totals['longform']:.2f}")
    return "\n".join(lines)
