"""DESIGN.md §6 as pure functions, proved against the hand-worked example in issue 023.

The worked example (verbatim from issues/023-scoring-model.md):

Niche `top5-countdown × dangerous-animals`, shorts, `now = 2026-09-01`, 90-day window.
Six channels; views are 90-day totals of their videos in the window:

| ch | subs | created    | 90d views | has an outlier video |
|----|------|------------|-----------|----------------------|
| A  | 2,000 | 2026-03-01 | 40,000  | yes |
| B  | 5,000 | 2026-01-15 | 5,000   | no  |
| C  | 9,000 | 2025-11-01 | 120,000 | yes |
| D  | 800,000 | 2019-05-01 | 900,000 | yes |
| E  | 60,000 | 2023-02-01 | 300,000 | no |
| F  | 9,500 | 2024-06-01 | 100,000 | yes  (small by subs, **too old** → not small) |

- small = {A, B, C} → 3 channels
- `small_outlier_rate` = 2/3 = **0.6667**
- total views = 1,465,000; small views = 165,000 → `newcomer_view_share` = **0.1126**
- top-3 = D+E+C = 1,320,000 → `concentration` = **0.9010**
- `opportunity` = 0.5×0.6667 + 0.3×0.1126 + 0.2×(1−0.9010) = 0.3333 + 0.0338 + 0.0198
  = **0.3869**; `|small| = 3 < 5` → flag `low_confidence`; cap 0.4 does not bite.
- monthly views of small channels = 90d ÷ 3 → A 13,333; B 1,667; C 40,000 →
  p50 = **13,333**, p25 = **7,500**, p75 = **26,667** (inclusive quantiles).
- RPM: table `animals_nature.shorts.mid = 0.08` USD; own actual RPM 0.10 →
  `calibration` = **1.25**; `rpm_usd` = 0.10; `usd_gbp` = 0.78 → `rpm_gbp` = **0.078**;
  `est_monthly_gbp` = 13.333 × 0.078 = **£1.04**.
- Effort: required steps `research, script, voiceover, visuals_stock, assembly, metadata,
  upload, qa`; coverage: script/voiceover/assembly/metadata/upload `automated`;
  research `partial` override 0.25; visuals_stock `partial` override 0.5; qa `manual`
  0.15 → per video **0.90 h**; × 20 videos → **18.0 h/month**.
- `score` = 1.04 / max(18.0, 2) = **£0.058 per manual hour**.

Every bold number is asserted below to 3 significant figures. Each channel's 90-day
total is split into six videos inside the window; where the table says "yes", one of
them is ≥ 3 × the channel's median and ≥ 10,000 views.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ytscout.audit import PipelineCoverage, Step, StepCoverage
from ytscout.scoring import ScoringConfigError, load_scoring, niche_scoring_config
from ytscout.scoring.effort import DISQUALIFIED, manual_hours_per_month, manual_hours_per_video
from ytscout.scoring.final import score
from ytscout.scoring.money import calibration, est_monthly_gbp, rpm_gbp, rpm_usd
from ytscout.scoring.opportunity import (
    LOW_CONFIDENCE,
    channel_median,
    concentration,
    is_small,
    newcomer_monthly_views,
    newcomer_view_share,
    opportunity,
    outlier_videos,
    small_channels,
    small_outlier_rate,
)
from ytscout.scoring.types import ChannelSample, NicheSample, VideoSample

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 1, tzinfo=UTC)

# The config the issue specifies, as literals: the same numbers config/scoring.yaml
# carries (test_yaml_matches_worked_example_config proves they agree).
CFG: dict = {
    "shorts_max_seconds": 180,
    "small_subs_max": 10000,
    "small_age_days": 365,
    "outlier_multiplier": 3,
    "outlier_window_videos": 30,
    "outlier_floor_views": {"shorts": 10000, "longform": 2000},
    "window_days": 90,
    "weights": {
        "small_outlier_rate": 0.5,
        "newcomer_view_share": 0.3,
        "inverse_concentration": 0.2,
    },
    "low_confidence_min_small": 5,
    "low_confidence_cap": 0.4,
    "top_n_concentration": 3,
    "videos_per_month": {"shorts": 20, "longform": 4},
    "manual_hours_floor_per_month": 2,
}


def sig3(value: float) -> float:
    """Round to 3 significant figures."""
    if value == 0:
        return 0.0
    return round(value, 2 - int(math.floor(math.log10(abs(value)))))


def videos(view_counts: list[int], *, duration_s: int = 45) -> list[VideoSample]:
    """One video a week inside the window, newest first, all Shorts unless told otherwise."""
    return [
        VideoSample(views=v, published_at=NOW - timedelta(days=7 * (i + 1)), duration_s=duration_s)
        for i, v in enumerate(view_counts)
    ]


def channel(cid: str, subs: int, created: str, view_counts: list[int]) -> ChannelSample:
    return ChannelSample(
        channel_id=cid,
        subs=subs,
        created_at=datetime.fromisoformat(created).replace(tzinfo=UTC),
        videos=videos(view_counts),
    )


# | ch | subs    | created    | 90d views | outlier |
A = channel("A", 2_000, "2026-03-01", [30_000, 2_000, 2_000, 2_000, 2_000, 2_000])  # 40,000 yes
B = channel("B", 5_000, "2026-01-15", [1_000, 1_000, 1_000, 1_000, 1_000])  # 5,000 no
C = channel("C", 9_000, "2025-11-01", [90_000, 6_000, 6_000, 6_000, 6_000, 6_000])  # 120,000 yes
D = channel("D", 800_000, "2019-05-01", [600_000] + [60_000] * 5)  # 900,000 yes
E = channel("E", 60_000, "2023-02-01", [50_000] * 6)  # 300,000 no
F = channel("F", 9_500, "2024-06-01", [70_000, 6_000, 6_000, 6_000, 6_000, 6_000])  # 100,000 yes
SAMPLE = NicheSample(channels=[A, B, C, D, E, F], fmt="shorts", now=NOW)


@dataclass(frozen=True)
class Row:
    mid: float


class Table:
    """The smallest thing that satisfies ``money.RpmTableLike``."""

    def __init__(self, rows: dict[tuple[str, str], float]) -> None:
        self.rows = rows

    def row(self, category: str, fmt: str) -> Row:
        return Row(self.rows[(category, fmt)])


TABLE = Table({("animals_nature", "shorts"): 0.08, ("animals_nature", "longform"): 5.0})

STEPS = [
    Step("research", "Topic research", 0.75),
    Step("script", "Script", 0.5),
    Step("voiceover", "Voiceover", 0.25),
    Step("visuals_stock", "Visual sourcing", 1.0, specific_footage_hours=2.5),
    Step("footage_original", "Original footage", 3.0, disqualifying_for_faceless=True),
    Step("presenter", "Presenter", 4.0, disqualifying_for_faceless=True),
    Step("assembly", "Assembly", 1.0),
    Step("thumbnail", "Thumbnail", 0.25, shorts_hours=0.0),
    Step("metadata", "Metadata", 0.15),
    Step("upload", "Upload", 0.1),
    Step("qa", "QA", 0.15, floor_hours=0.1),
    Step("community", "Community", 0.1),
]
COVERAGE = PipelineCoverage(
    audited_at="2026-09-01",
    pipeline_commit="abc1234",
    formats_supported=("shorts", "longform"),
    steps={
        "research": StepCoverage("research", "partial", manual_hours_override=0.25),
        "script": StepCoverage("script", "automated"),
        "voiceover": StepCoverage("voiceover", "automated"),
        "visuals_stock": StepCoverage("visuals_stock", "partial", manual_hours_override=0.5),
        "assembly": StepCoverage("assembly", "automated"),
        "metadata": StepCoverage("metadata", "automated"),
        "upload": StepCoverage("upload", "automated"),
        "qa": StepCoverage("qa", "manual"),
        "presenter": StepCoverage("presenter", "manual"),
        "footage_original": StepCoverage("footage_original", "manual"),
    },
)
REQUIRED = [
    "research",
    "script",
    "voiceover",
    "visuals_stock",
    "assembly",
    "metadata",
    "upload",
    "qa",
]


# ---------------------------------------------------------------- the fixture matches the table


def test_fixture_matches_the_table() -> None:
    totals = {c.channel_id: sum(v.views for v in c.videos) for c in SAMPLE.channels}
    assert totals == {
        "A": 40_000,
        "B": 5_000,
        "C": 120_000,
        "D": 900_000,
        "E": 300_000,
        "F": 100_000,
    }
    has_outlier = {c.channel_id: bool(outlier_videos(c, SAMPLE, CFG)) for c in SAMPLE.channels}
    assert has_outlier == {"A": True, "B": False, "C": True, "D": True, "E": False, "F": True}


# ---------------------------------------------------------------- §6.1


def test_small_channels_are_a_b_c() -> None:
    assert [c.channel_id for c in small_channels(SAMPLE, CFG)] == ["A", "B", "C"]
    assert is_small(F, SAMPLE, CFG) is False  # small by subs, too old
    assert is_small(D, SAMPLE, CFG) is False


def test_small_outlier_rate() -> None:
    assert sig3(small_outlier_rate(SAMPLE, CFG)) == 0.667


def test_newcomer_view_share() -> None:
    assert sig3(newcomer_view_share(SAMPLE, CFG)) == 0.113


def test_concentration() -> None:
    assert sig3(concentration(SAMPLE, CFG)) == 0.901


def test_opportunity_is_low_confidence_but_uncapped() -> None:
    value, flags = opportunity(SAMPLE, CFG)
    assert sig3(value) == 0.387
    assert flags == [LOW_CONFIDENCE]


# ---------------------------------------------------------------- §6.2


def test_newcomer_monthly_views_quartiles() -> None:
    p25, p50, p75 = newcomer_monthly_views(SAMPLE, CFG)
    assert sig3(p25) == 7_500
    assert sig3(p50) == 13_300
    assert sig3(p75) == 26_700


# ---------------------------------------------------------------- §6.3


def test_money() -> None:
    cal = calibration(0.10, TABLE)
    assert sig3(cal) == 1.25
    assert sig3(rpm_usd("animals_nature", "shorts", TABLE, cal)) == 0.1
    gbp = rpm_gbp("animals_nature", "shorts", TABLE, cal, usd_gbp=0.78)
    assert sig3(gbp) == 0.078
    _, p50, _ = newcomer_monthly_views(SAMPLE, CFG)
    assert sig3(est_monthly_gbp(p50, gbp)) == 1.04


# ---------------------------------------------------------------- §6.4


def test_effort() -> None:
    per_video, flags = manual_hours_per_video(REQUIRED, STEPS, COVERAGE, "shorts")
    assert sig3(per_video) == 0.9
    assert flags == []
    assert sig3(manual_hours_per_month(per_video, CFG["videos_per_month"]["shorts"])) == 18.0


# ---------------------------------------------------------------- §6.5, end to end


def test_final_score_end_to_end() -> None:
    cal = calibration(0.10, TABLE)
    gbp = rpm_gbp("animals_nature", "shorts", TABLE, cal, usd_gbp=0.78)
    _, p50, _ = newcomer_monthly_views(SAMPLE, CFG)
    money = est_monthly_gbp(p50, gbp)
    per_video, _ = manual_hours_per_video(REQUIRED, STEPS, COVERAGE, "shorts")
    hours = manual_hours_per_month(per_video, CFG["videos_per_month"]["shorts"])
    assert sig3(score(money, hours, CFG["manual_hours_floor_per_month"])) == 0.0578


# ---------------------------------------------------------------- extra cases the issue asks for


def test_cap_bites_with_four_small_channels() -> None:
    # Four young, tiny channels, each with an outlier, and nobody else: rate 1.0, share 1.0,
    # concentration 0.75 (top 3 of 4 equal channels) → 0.5 + 0.3 + 0.05 = 0.85 → capped 0.4.
    four = [channel(f"S{i}", 500, "2026-06-01", [40_000, 5_000, 5_000, 5_000]) for i in range(4)]
    sample = NicheSample(four, "shorts", NOW)
    raw = (
        0.5 * small_outlier_rate(sample, CFG)
        + 0.3 * newcomer_view_share(sample, CFG)
        + 0.2 * (1 - concentration(sample, CFG))
    )
    assert raw > 0.6
    value, flags = opportunity(sample, CFG)
    assert value == 0.4
    assert flags == [LOW_CONFIDENCE]
    # A fifth small channel lifts the cap and the flag.
    five = NicheSample(
        [*four, channel("S4", 500, "2026-06-01", [40_000, 5_000, 5_000])], "shorts", NOW
    )
    value, flags = opportunity(five, CFG)
    assert value > 0.4
    assert flags == []


def test_presenter_disqualifies() -> None:
    per_video, flags = manual_hours_per_video([*REQUIRED, "presenter"], STEPS, COVERAGE, "shorts")
    assert math.isinf(per_video)
    assert flags == [DISQUALIFIED]
    hours = manual_hours_per_month(per_video, 20)
    assert math.isinf(hours)
    assert score(100.0, hours, 2) == 0.0


def test_floor_of_two_hours_per_month() -> None:
    assert score(10.0, 0.5, 2) == 5.0
    assert score(10.0, 4.0, 2) == 2.5


# ---------------------------------------------------------------- config is the source of numbers


def test_outlier_multiplier_from_config_changes_the_outlier_set() -> None:
    # F's big video is 70,000 against a median of 6,000: 11.7×, an outlier at ×3 and at ×12? No:
    # at ×12 the threshold is 72,000, so F drops out while D (10×... 600,000 vs 60,000) stays.
    assert outlier_videos(F, SAMPLE, CFG) == [F.videos[0]]
    assert outlier_videos(F, SAMPLE, {**CFG, "outlier_multiplier": 12}) == []
    assert outlier_videos(D, SAMPLE, {**CFG, "outlier_multiplier": 12}) == []
    assert outlier_videos(D, SAMPLE, {**CFG, "outlier_multiplier": 2}) == [D.videos[0]]
    # E has no outlier at ×3 (all equal); at ×1 every video clears the median and the floor.
    assert len(outlier_videos(E, SAMPLE, {**CFG, "outlier_multiplier": 1})) == 6


def test_outlier_floor_from_config() -> None:
    # A's 30,000-view video clears the 10,000 Shorts floor but not a 50,000 one.
    assert outlier_videos(A, SAMPLE, CFG) == [A.videos[0]]
    floors = {"shorts": 50_000, "longform": 2000}
    assert outlier_videos(A, SAMPLE, {**CFG, "outlier_floor_views": floors}) == []


def test_small_thresholds_from_config() -> None:
    # Raise the age limit to 3 years and F (created 2024-06-01) becomes small.
    cfg = {**CFG, "small_age_days": 3 * 365}
    assert [c.channel_id for c in small_channels(SAMPLE, cfg)] == ["A", "B", "C", "F"]
    # Lower the subs cap to 5,000 and only A survives.
    cfg = {**CFG, "small_subs_max": 5000}
    assert [c.channel_id for c in small_channels(SAMPLE, cfg)] == ["A"]


def test_weights_and_top_n_from_config() -> None:
    cfg = {
        **CFG,
        "weights": {
            "small_outlier_rate": 1.0,
            "newcomer_view_share": 0.0,
            "inverse_concentration": 0.0,
        },
    }
    value, _ = opportunity(cfg=cfg, sample=SAMPLE)
    assert sig3(value) == 0.4  # 0.667 capped (3 small channels)
    assert sig3(concentration(SAMPLE, {**CFG, "top_n_concentration": 1})) == 0.614  # D alone


def test_missing_config_key_raises() -> None:
    cfg = {k: v for k, v in CFG.items() if k != "window_days"}
    with pytest.raises(KeyError, match="window_days"):
        newcomer_view_share(SAMPLE, cfg)


# ---------------------------------------------------------------- edge cases


def test_unknown_subs_or_age_is_not_small_but_counts_in_totals() -> None:
    hidden = ChannelSample("H", None, datetime(2026, 6, 1, tzinfo=UTC), videos([100_000]))
    sample = NicheSample([A, hidden], "shorts", NOW)
    assert is_small(hidden, sample, CFG) is None
    assert [c.channel_id for c in small_channels(sample, CFG)] == ["A"]
    assert sig3(newcomer_view_share(sample, CFG)) == sig3(40_000 / 140_000)


def test_videos_outside_the_window_or_format_are_ignored_for_views_but_not_the_median() -> None:
    old = VideoSample(views=500_000, published_at=NOW - timedelta(days=91), duration_s=45)
    longform = VideoSample(views=500_000, published_at=NOW - timedelta(days=1), duration_s=600)
    ch = ChannelSample("X", 1000, datetime(2026, 6, 1, tzinfo=UTC), [*A.videos, old, longform])
    sample = NicheSample([ch], "shorts", NOW)
    assert newcomer_monthly_views(sample, CFG) == (40_000 / 3,) * 3
    # The 91-day-old Short counts towards the median (it is one of the newest 30) but can
    # never be an outlier itself; the long-form video is invisible to a Shorts sample.
    assert outlier_videos(ch, sample, CFG) == [A.videos[0]]
    # channel_median itself is format-blind: the newest two are the 500,000 long-form video
    # and A's 30,000 Short.
    assert channel_median(ch.videos, last_n=2) == 265_000


def test_channel_median_uses_only_the_newest_n() -> None:
    assert channel_median(D.videos, last_n=30) == 60_000
    assert channel_median(D.videos, last_n=1) == 600_000
    assert channel_median([], last_n=30) is None


def test_empty_niche() -> None:
    sample = NicheSample([], "shorts", NOW)
    assert small_outlier_rate(sample, CFG) == 0.0
    assert newcomer_view_share(sample, CFG) == 0.0
    assert concentration(sample, CFG) == 1.0
    assert opportunity(sample, CFG) == (0.0, [LOW_CONFIDENCE])
    assert newcomer_monthly_views(sample, CFG) is None


def test_single_small_channel_quartiles_collapse() -> None:
    sample = NicheSample([A, D], "shorts", NOW)
    assert newcomer_monthly_views(sample, CFG) == (40_000 / 3,) * 3


def test_uncalibrated_when_own_rpm_unknown() -> None:
    assert calibration(None, TABLE) == 1.0
    assert calibration(0.0, TABLE) == 1.0
    assert rpm_gbp("animals_nature", "longform", TABLE, 1.0, usd_gbp=0.78) == pytest.approx(3.9)
    with pytest.raises(ValueError):
        rpm_gbp("animals_nature", "shorts", TABLE, 1.0, usd_gbp=0)


def test_effort_honours_shorts_hours_floor_and_missing_coverage() -> None:
    # thumbnail: manual by default (no coverage entry) but shorts_hours = 0 → 0 for Shorts,
    # 0.25 for long-form. community: no coverage entry → manual 0.1.
    shorts, _ = manual_hours_per_video(["thumbnail", "community"], STEPS, COVERAGE, "shorts")
    assert shorts == pytest.approx(0.1)
    longform, _ = manual_hours_per_video(["thumbnail", "community"], STEPS, COVERAGE, "longform")
    assert longform == pytest.approx(0.35)
    # qa automated still costs its floor.
    auto_qa = PipelineCoverage("d", "c", ("shorts",), {"qa": StepCoverage("qa", "automated")})
    hours, _ = manual_hours_per_video(["qa"], STEPS, auto_qa, "shorts")
    assert hours == pytest.approx(0.1)
    # A format the pipeline does not support is costed fully manual.
    hours, _ = manual_hours_per_video(["research", "assembly"], STEPS, auto_qa, "longform")
    assert hours == pytest.approx(1.75)
    with pytest.raises(KeyError, match="no_such_step"):
        manual_hours_per_video(["no_such_step"], STEPS, COVERAGE, "shorts")


# ---------------------------------------------------------------- config file


def test_yaml_matches_worked_example_config() -> None:
    doc = load_scoring(REPO_ROOT / "config" / "scoring.yaml")
    assert niche_scoring_config(doc) == CFG
    # The keys shared with the collector and the competitor metrics must not drift.
    assert doc["niche_validation"]["small_subs_max"] == CFG["small_subs_max"]
    assert doc["niche_validation"]["small_age_days"] == CFG["small_age_days"]
    assert doc["competitor_metrics"]["outlier_multiplier"] == CFG["outlier_multiplier"]
    assert doc["competitor_metrics"]["outlier_window_videos"] == CFG["outlier_window_videos"]


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"niche_scoring": None}, "niche_scoring"),
        ({"niche_scoring": {**CFG, "window_days": 0}}, "window_days"),
        ({"niche_scoring": {**CFG, "low_confidence_cap": 1.5}}, "low_confidence_cap"),
        ({"niche_scoring": {**CFG, "weights": {"small_outlier_rate": 1}}}, "weights"),
        (
            {"niche_scoring": {**CFG, "weights": {**CFG["weights"], "inverse_concentration": 0.5}}},
            "sum to 1",
        ),
        ({"niche_scoring": {**CFG, "outlier_floor_views": {"shorts": 1}}}, "outlier_floor_views"),
    ],
)
def test_niche_scoring_config_rejects(patch: dict, message: str) -> None:
    doc = {**load_scoring(REPO_ROOT / "config" / "scoring.yaml"), **patch}
    with pytest.raises(ScoringConfigError, match=message):
        niche_scoring_config(doc)


def test_scoring_package_never_touches_the_db() -> None:
    for path in (REPO_ROOT / "src" / "ytscout" / "scoring").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "sqlite" not in text and "conn" not in text, path
