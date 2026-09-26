"""``scout sensitivity``: re-score niches under a grid of thresholds, in memory (issue 028).

DESIGN.md §13 admits the small-channel and outlier thresholds are arbitrary. This runs
every ``scored``/``tracking`` niche through issue 023's scorers once per cell of the grid

    outlier_multiplier ∈ {2, 3, 4} × small_subs_max ∈ {5000, 10000, 20000}
    × small_age_days ∈ {180, 365, 540}

(27 cells) with a patched copy of the ``niche_scoring:`` config, and reports how much the
score, the opportunity and the rank of each niche move. Nothing is written to the DB and
no API is called: the caller hands over a read-only copy of the database.

Structural note the report repeats: ``score`` is £/month ÷ manual hours (§6.5) and
``opportunity`` (§6.1) does not enter it. ``outlier_multiplier`` therefore moves the
opportunity only; ``small_subs_max`` and ``small_age_days`` move both, through the small
set that feeds ``newcomer_monthly_views`` (§6.2) and so the £/month.
"""

from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ytscout.audit import PipelineCoverage, Step
from ytscout.scoring import money
from ytscout.scout import score as scout_score
from ytscout.store import repo, to_utc_iso

# Axis -> the values tried, in the order the issue lists them. The middle value of each
# axis is config/scoring.yaml's default (test_sensitivity proves it).
GRID: dict[str, tuple[float, ...]] = {
    "outlier_multiplier": (2, 3, 4),
    "small_subs_max": (5000, 10000, 20000),
    "small_age_days": (180, 365, 540),
}
AXES: tuple[str, ...] = tuple(GRID)
TOP_N = 3
THIN_SAMPLE_BELOW = 3
DEFAULT_OUT = "docs/sensitivity.md"

# Statuses the default run covers: scored, or tracked after scoring. Validated-but-untagged
# niches have no score to move.
SENSITIVITY_STATUSES: tuple[str, ...] = (repo.NICHE_STATUS_SCORED, *repo.NICHE_STATUSES_TRACKING)

Setting = tuple[float, ...]


def settings_grid(grid: Mapping[str, tuple[float, ...]] = GRID) -> list[Setting]:
    """Every combination of the grid's axes, ``itertools.product`` order (27 for GRID)."""
    return list(itertools.product(*(grid[axis] for axis in grid)))


def default_setting(
    cfg: Mapping[str, Any], grid: Mapping[str, tuple[float, ...]] = GRID
) -> Setting:
    """The grid cell that equals the config's own thresholds."""
    return tuple(cfg[axis] for axis in grid)


def patched(cfg: Mapping[str, Any], setting: Setting, axes: Iterable[str] = AXES) -> dict[str, Any]:
    """``cfg`` with the grid axes overridden; the original is untouched."""
    return {**cfg, **dict(zip(axes, setting, strict=True))}


def setting_label(setting: Setting, axes: Iterable[str] = AXES) -> str:
    """``outlier_multiplier=2 small_subs_max=5000 small_age_days=180``."""
    return " ".join(f"{axis}={value:g}" for axis, value in zip(axes, setting, strict=True))


@dataclass(frozen=True)
class Cell:
    """One grid setting and the niches scored under it."""

    setting: Setting
    scores: list[scout_score.NicheScore]

    def ranked(self) -> list[scout_score.NicheScore]:
        return sorted(self.scores, key=lambda s: (-s.score, s.niche_id))

    def rank_of(self, niche_id: int) -> int:
        for i, s in enumerate(self.ranked(), 1):
            if s.niche_id == niche_id:
                return i
        raise LookupError(f"niche {niche_id} not in this cell")

    def top(self, n: int = TOP_N) -> list[int]:
        return [s.niche_id for s in self.ranked()[:n]]


@dataclass(frozen=True)
class NicheSensitivity:
    """How one niche moved across the grid."""

    niche_id: int
    label: str
    format: str
    score_min: float
    score_default: float
    score_max: float
    opportunity_min: float
    opportunity_default: float
    opportunity_max: float
    rank_default: int
    rank_min: int
    rank_max: int
    # Grid settings (out of len(cells)) under which the niche keeps its default rank.
    stable_settings: int
    # The first grid setting (product order) at which the score is lowest / highest.
    score_min_setting: Setting = ()
    score_max_setting: Setting = ()

    @property
    def score_spread(self) -> float | None:
        """``score_max ÷ score_min``, or ``None`` when the lowest score is 0."""
        return self.score_max / self.score_min if self.score_min > 0 else None


@dataclass
class SensitivityResult:
    default: Setting
    cells: list[Cell] = field(default_factory=list)
    skipped_untagged: int = 0
    computed_at: str = ""

    @property
    def niche_ids(self) -> list[int]:
        return [s.niche_id for s in self.default_cell.scores]

    @property
    def default_cell(self) -> Cell:
        for cell in self.cells:
            if cell.setting == self.default:
                return cell
        raise LookupError("the default setting is not in the grid")

    def cell(self, setting: Setting) -> Cell:
        for cell in self.cells:
            if cell.setting == setting:
                return cell
        raise LookupError(f"setting {setting} is not in the grid")

    def per_niche(self) -> list[NicheSensitivity]:
        """One row per niche, default rank order."""
        rows = []
        base = self.default_cell
        for s in base.ranked():
            nid = s.niche_id
            scores = [c.rank_of(nid) for c in self.cells]
            values = [next(x for x in c.scores if x.niche_id == nid).values for c in self.cells]
            score_values = [v["score"] or 0.0 for v in values]
            opp_values = [v["opportunity"] or 0.0 for v in values]
            default_rank = base.rank_of(nid)
            lowest = min(range(len(score_values)), key=score_values.__getitem__)
            highest = max(range(len(score_values)), key=score_values.__getitem__)
            rows.append(
                NicheSensitivity(
                    niche_id=nid,
                    label=s.label,
                    format=s.format,
                    score_min=min(score_values),
                    score_default=s.score,
                    score_max=max(score_values),
                    opportunity_min=min(opp_values),
                    opportunity_default=s.values["opportunity"] or 0.0,
                    opportunity_max=max(opp_values),
                    rank_default=default_rank,
                    rank_min=min(scores),
                    rank_max=max(scores),
                    stable_settings=sum(1 for r in scores if r == default_rank),
                    score_min_setting=self.cells[lowest].setting,
                    score_max_setting=self.cells[highest].setting,
                )
            )
        return rows

    def top_changes(self, n: int = TOP_N) -> list[tuple[Setting, int]]:
        """Each non-default setting with how many of the default top-``n`` positions it
        changes (a different niche at that position), most disruptive first."""
        base = self.default_cell.top(n)
        out = []
        for cell in self.cells:
            if cell.setting == self.default:
                continue
            top = cell.top(n)
            changed = sum(1 for a, b in itertools.zip_longest(base, top) if a != b)
            out.append((cell.setting, changed))
        out.sort(key=lambda item: (-item[1], item[0]))
        return out

    def most_disruptive(self, n: int = TOP_N) -> tuple[Setting, int] | None:
        """The setting that changes the most top-``n`` positions, or ``None`` when no
        setting changes any."""
        changes = self.top_changes(n)
        if not changes or changes[0][1] == 0:
            return None
        return changes[0]

    def axis_effects(self) -> dict[str, int]:
        """Per axis: how many settings that differ from the default *only* on that axis
        change the ranking at all."""
        out: dict[str, int] = {}
        for i, axis in enumerate(AXES):
            count = 0
            for cell in self.cells:
                off_axis = [v for j, v in enumerate(cell.setting) if j != i]
                base = [v for j, v in enumerate(self.default) if j != i]
                if off_axis != base or cell.setting == self.default:
                    continue
                if cell.top(len(cell.scores)) != self.default_cell.top(len(cell.scores)):
                    count += 1
            out[axis] = count
        return out


def target_niches(
    conn: sqlite3.Connection, niche_ids: Collection[int] | None = None
) -> tuple[list[sqlite3.Row], list[int]]:
    """(niches to run, ids that were asked for but do not exist). Default: every niche
    in ``SENSITIVITY_STATUSES``. With ``niche_ids`` the status does not matter."""
    if niche_ids is None:
        return [n for n in repo.scorable_niches(conn) if n["status"] in SENSITIVITY_STATUSES], []
    rows, missing = [], []
    for nid in niche_ids:
        row = repo.get_niche(conn, nid)
        if row is None:
            missing.append(nid)
        else:
            rows.append(row)
    return rows, missing


def run_grid(
    conn: sqlite3.Connection,
    niches: Iterable[sqlite3.Row],
    *,
    cfg: Mapping[str, Any],
    rpm: money.RpmTableLike,
    steps: Iterable[Step],
    coverage: PipelineCoverage,
    usd_gbp: float,
    now: datetime,
    grid: Mapping[str, tuple[float, ...]] = GRID,
) -> SensitivityResult:
    """Score every tagged niche under every grid setting. Reads only; the stored relevance
    verdicts stand (049) and no ``niche_scores`` row is appended."""
    steps = list(steps)
    tagged = [n for n in niches if n["tag_prompt_hash"] is not None]
    result = SensitivityResult(
        default=default_setting(cfg, grid),
        skipped_untagged=sum(1 for n in niches if n["tag_prompt_hash"] is None),
        computed_at=to_utc_iso(now),
    )
    calibration = scout_score.calibration_from_db(
        conn, rpm, now=now, shorts_max_seconds=int(cfg["shorts_max_seconds"])
    )
    for setting in settings_grid(grid):
        patched_cfg = patched(cfg, setting, grid)
        scores = [
            scout_score.score_niche(
                conn,
                niche,
                cfg=patched_cfg,
                rpm=rpm,
                steps=steps,
                coverage=coverage,
                usd_gbp=usd_gbp,
                calibration=calibration,
                now=now,
            )
            for niche in tagged
        ]
        result.cells.append(Cell(setting=setting, scores=scores))
    return result


# ---------------------------------------------------------------- reports


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _table(rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for n, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _md_table(rows: list[tuple[str, ...]], align_right_from: int = 0) -> str:
    head, *body = rows
    sep = [
        "---:" if align_right_from and i >= align_right_from else "---" for i in range(len(head))
    ]
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(sep) + " |"]
    lines.extend("| " + " | ".join(r) + " |" for r in body)
    return "\n".join(lines)


def _summary_rows(result: SensitivityResult) -> list[tuple[str, ...]]:
    n = len(result.cells)
    rows = [
        (
            "id",
            "label",
            "format",
            "score min",
            "default",
            "max",
            "opp min",
            "default",
            "max",
            "rank",
            "range",
            "stable",
        )
    ]
    for r in result.per_niche():
        rows.append(
            (
                str(r.niche_id),
                r.label[:48],
                r.format,
                _fmt(r.score_min),
                _fmt(r.score_default),
                _fmt(r.score_max),
                _fmt(r.opportunity_min),
                _fmt(r.opportunity_default),
                _fmt(r.opportunity_max),
                str(r.rank_default),
                f"{r.rank_min}-{r.rank_max}",
                f"{r.stable_settings}/{n}",
            )
        )
    return rows


def _grid_rows(result: SensitivityResult) -> list[tuple[str, ...]]:
    ids = result.niche_ids
    head: tuple[str, ...] = (
        *AXES,
        *(f"#{nid} rank" for nid in ids),
        *(f"#{nid} score" for nid in ids),
    )
    rows = [head]
    for cell in result.cells:
        by_id = {s.niche_id: s for s in cell.scores}
        marker = " *" if cell.setting == result.default else ""
        rows.append(
            (
                *(f"{v:g}" for v in cell.setting[:-1]),
                f"{cell.setting[-1]:g}{marker}",
                *(str(cell.rank_of(nid)) for nid in ids),
                *(_fmt(by_id[nid].score) for nid in ids),
            )
        )
    return rows


def _lines_of_reading(result: SensitivityResult) -> list[str]:
    n = len(result.cells)
    effects = result.axis_effects()
    lines = []
    for axis, count in effects.items():
        others = 2  # the two other values of a three-point axis
        if count == 0:
            lines.append(f"- `{axis}` alone never changes the ranking ({others} settings tried).")
        else:
            lines.append(
                f"- `{axis}` alone changes the ranking in {count} of {others} settings tried."
            )
    worst = result.most_disruptive()
    if worst is None:
        lines.append(f"- No setting changes the top-{TOP_N}: the default ranking holds in all {n}.")
    else:
        setting, changed = worst
        lines.append(
            f"- The setting that most changes the top-{TOP_N} is `{setting_label(setting)}`"
            f" ({changed} of {TOP_N} positions differ from the default)."
        )
    per = result.per_niche()
    fully_stable = [r for r in per if r.stable_settings == n]
    if per and len(fully_stable) == len(per):
        lines.append(f"- Every niche keeps its default rank in all {n} settings.")
    elif per:
        moved = [r for r in per if r.stable_settings < n]
        worst_niche = min(moved, key=lambda r: r.stable_settings)
        lines.append(
            f"- Least stable: #{worst_niche.niche_id} {worst_niche.label} keeps rank"
            f" {worst_niche.rank_default} in {worst_niche.stable_settings} of {n} settings"
            f" (range {worst_niche.rank_min}-{worst_niche.rank_max})."
        )
    # Ranks can hold while the £ figures behind them swing: name the widest swing.
    widest = max(per, key=_spread_key, default=None)
    if widest is not None and widest.score_max > widest.score_min:
        spread = widest.score_spread
        how = f"{spread:.1f}x" if spread is not None else "from 0"
        lines.append(
            f"- Widest score swing: #{widest.niche_id} {widest.label} spans"
            f" {_fmt(widest.score_min)}-{_fmt(widest.score_max)} ({how}), lowest at"
            f" `{setting_label(widest.score_min_setting)}`, highest at"
            f" `{setting_label(widest.score_max_setting)}`."
        )
    return lines


def _spread_key(row: NicheSensitivity) -> tuple[float, float]:
    """Order niches by how far their score swings: a swing that reaches 0 counts as
    infinite, then by the max ÷ min ratio."""
    if row.score_max == row.score_min:
        return (0.0, 0.0)
    return (float("inf"), 0.0) if row.score_min == 0 else (row.score_spread or 0.0, 0.0)


def format_summary(result: SensitivityResult) -> str:
    """What ``scout sensitivity`` prints: the per-niche table and the reading."""
    per = result.per_niche()
    parts = []
    if len(per) < THIN_SAMPLE_BELOW:
        parts.append(
            f"note: only {len(per)} niche(s) scored; the sample is thin and the ranking says little"
        )
    parts.append(_table(_summary_rows(result)))
    parts.append(
        f"{len(result.cells)} settings x {len(per)} niche(s), default"
        f" {setting_label(result.default)} (* in the grid table)"
    )
    parts.extend(line.lstrip("- ") for line in _lines_of_reading(result))
    return "\n".join(parts)


def format_report(result: SensitivityResult, *, scoring_path: str = "config/scoring.yaml") -> str:
    """``docs/sensitivity.md``: the tables and a short reading of them."""
    per = result.per_niche()
    n = len(result.cells)
    out = [
        "# Sensitivity check: do the thresholds change the ranking?",
        "",
        f"Generated {result.computed_at} by `ytscout scout sensitivity` (issue 028) on"
        f" {len(per)} niche(s), {n} settings. Re-run it for current numbers. Nothing here"
        f" is written to the DB and `{scoring_path}` is unchanged: changing a default is"
        " Nagz's call.",
        "",
        "The grid is every combination of "
        + ", ".join(
            f"`{axis}` ∈ {{{', '.join(f'{v:g}' for v in values)}}}" for axis, values in GRID.items()
        )
        + f". The default is `{setting_label(result.default)}`.",
        "",
    ]
    if len(per) < THIN_SAMPLE_BELOW:
        out.extend(
            [
                f"**The sample is thin**: {len(per)} niche(s). Rank stability over so few"
                " niches says little; read the score ranges instead.",
                "",
            ]
        )
    if result.skipped_untagged:
        out.extend([f"{result.skipped_untagged} untagged niche(s) skipped.", ""])
    out.extend(
        [
            "## What can move",
            "",
            "`score` is £/month ÷ manual hours/month (DESIGN.md §6.5) and `opportunity`"
            " (§6.1) does not enter it. So `outlier_multiplier` moves the opportunity column"
            " only and can never change a rank. `small_subs_max` and `small_age_days` decide"
            " which channels are small, which moves both the opportunity and the newcomer"
            " views (§6.2) that the £/month, and so the score and the rank, are built on."
            " Manual hours do not depend on any grid axis.",
            "",
            "## Per niche: score, opportunity and rank across the grid",
            "",
            "`rank` is the rank under the default; `range` the lowest and highest rank in"
            f" the grid; `stable` how many of the {n} settings keep the default rank."
            " Ranks are over every niche in the run, both formats together, as `score"
            " --niches` prints them; the dashboard ranks each format on its own.",
            "",
            _md_table(_summary_rows(result), align_right_from=3),
            "",
            "## Reading",
            "",
            *_lines_of_reading(result),
            "",
            "## Every setting",
            "",
            "One row per grid cell; `*` marks the default.",
            "",
            _md_table(_grid_rows(result), align_right_from=len(AXES)),
            "",
        ]
    )
    return "\n".join(out)
