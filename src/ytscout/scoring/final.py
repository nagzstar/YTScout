"""DESIGN.md §6.5: £ per manual hour. One pure function."""

from __future__ import annotations

import math


def score(est_monthly_gbp: float, manual_hours_per_month: float, floor: float) -> float:
    """``est_monthly_gbp ÷ max(manual_hours_per_month, floor)``.

    The floor stops a fully automated niche scoring infinity; a disqualified niche
    (``inf`` hours) scores 0.
    """
    if floor <= 0:
        raise ValueError(f"manual_hours_floor_per_month must be positive, got {floor}")
    if math.isinf(manual_hours_per_month):
        return 0.0
    return est_monthly_gbp / max(manual_hours_per_month, floor)
