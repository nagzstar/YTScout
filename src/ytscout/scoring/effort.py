"""DESIGN.md §6.4: manual hours per video and per month. Pure functions.

Reuses ``ytscout.audit`` for the step list, the coverage file and the one-step
arithmetic (``effective_hours``); this module only sums the steps a niche requires.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from ytscout.audit import PipelineCoverage, Step, StepCoverage, effective_hours

DISQUALIFIED = "disqualified"


def manual_hours_per_video(
    required_step_ids: Iterable[str],
    steps: Iterable[Step] | Mapping[str, Step],
    coverage: PipelineCoverage,
    fmt: str,
) -> tuple[float, list[str]]:
    """Hours a human still spends per video on the steps the niche requires.

    Per step: ``automated`` → 0; ``partial`` → ``manual_hours_override`` (never above the
    step's base hours); ``manual`` → ``default_hours`` (``shorts_hours`` for Shorts when
    set); ``floor_hours`` applied last. A required step the coverage file does not list is
    costed manual. A required step that is ``disqualifying_for_faceless`` makes the total
    ``inf`` and flags the niche ``disqualified``. An unknown step id raises ``KeyError``.
    """
    by_id = dict(steps) if isinstance(steps, Mapping) else {s.id: s for s in steps}
    if fmt not in ("shorts", "longform"):
        raise ValueError(f"fmt must be 'shorts' or 'longform', got {fmt!r}")
    supported = fmt in coverage.formats_supported
    total = 0.0
    flags: list[str] = []
    for step_id in required_step_ids:
        if step_id not in by_id:
            raise KeyError(f"unknown production step {step_id!r}")
        step = by_id[step_id]
        if step.disqualifying_for_faceless:
            if DISQUALIFIED not in flags:
                flags.append(DISQUALIFIED)
            total = math.inf
            continue
        cov = coverage.steps.get(step_id) or StepCoverage(step_id, "manual")
        total += effective_hours(step, cov, fmt, supported=supported)  # type: ignore[arg-type]
    return total, flags


def manual_hours_per_month(per_video: float, videos_per_month: float) -> float:
    """§6.4: per-video hours × videos a month. ``inf`` stays ``inf``."""
    if videos_per_month < 0:
        raise ValueError(f"videos_per_month must be ≥ 0, got {videos_per_month}")
    if math.isinf(per_video):
        return math.inf
    return per_video * videos_per_month
