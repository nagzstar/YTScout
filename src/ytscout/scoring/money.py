"""DESIGN.md §6.3: RPM, calibration and estimated £/month. Pure functions.

The RPM table is anything with ``row(category, fmt).mid`` in USD per 1,000 views:
``ytscout.scout.propose.RpmTable`` in production, a two-line fake in tests. This module
must not import that loader (it pulls in the DB).
"""

from __future__ import annotations

from typing import Protocol

REFERENCE_ROW: tuple[str, str] = ("animals_nature", "shorts")
UNCALIBRATED = "uncalibrated"
# What the flag means when it shows: the own channel has no monetised views in the
# calibration window (not yet in the Partner Programme, 050), so RPMs are the table's own.
UNCALIBRATED_LABEL = "uncalibrated: no monetised views yet"
FLAG_LABELS: dict[str, str] = {UNCALIBRATED: UNCALIBRATED_LABEL}


def flag_label(flag: str) -> str:
    """The reader-facing text for a confidence flag; the bare flag when it has none."""
    return FLAG_LABELS.get(flag, flag)


class RpmRowLike(Protocol):
    @property
    def mid(self) -> float: ...


class RpmTableLike(Protocol):
    def row(self, category: str, fmt: str) -> RpmRowLike: ...


def calibration(
    own_rpm_usd: float | None, table: RpmTableLike, ref: tuple[str, str] = REFERENCE_ROW
) -> float:
    """Own actual RPM ÷ the table's ``mid`` for the reference row (the own channel's
    category × format). 1.0 when the own RPM is unknown (no Analytics token) or
    zero; the caller shows an ``uncalibrated`` badge in that case.
    """
    if own_rpm_usd is None or own_rpm_usd <= 0:
        return 1.0
    reference = table.row(*ref).mid
    if reference <= 0:
        raise ValueError(f"rpm table mid for {ref} must be positive, got {reference}")
    return own_rpm_usd / reference


def rpm_usd(category: str, fmt: str, table: RpmTableLike, calibration: float) -> float:
    """The table's ``mid`` for the niche's category × format, scaled by calibration."""
    return table.row(category, fmt).mid * calibration


def rpm_gbp(
    category: str, fmt: str, table: RpmTableLike, calibration: float, usd_gbp: float
) -> float:
    """§6.3 ``rpm_gbp``: the calibrated USD RPM converted at the fixed ``usd_gbp`` rate."""
    if usd_gbp <= 0:
        raise ValueError(f"usd_gbp must be positive, got {usd_gbp}")
    return rpm_usd(category, fmt, table, calibration) * usd_gbp


def est_monthly_gbp(views: float, rpm_gbp: float) -> float:
    """§6.3: monthly views ÷ 1,000 × £ RPM."""
    return views / 1000.0 * rpm_gbp
