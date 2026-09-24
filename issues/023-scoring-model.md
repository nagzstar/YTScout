# 023 — The scoring model as pure functions, tested against a hand-worked example

**Type**: AFK
**Blocked by**: 002
**Add dirs**: none
**Covers**: src/ytscout/scoring/{opportunity,money,effort,final,types}.py, config/scoring.yaml, tests/test_scoring.py
**Milestone**: M4

## Why

`DESIGN.md §6` is the whole point of the tool. It must be implemented exactly, be
tunable from YAML, and be proven against numbers a person worked out by hand — below.

## Scope

- `scoring/types.py`: `VideoSample(views, published_at, duration_s)`,
  `ChannelSample(channel_id, subs, created_at, videos: list[VideoSample])`,
  `NicheSample(channels: list[ChannelSample], fmt, now)`.
- `config/scoring.yaml` (extend the file from 004/006; every number below has a key):
  `shorts_max_seconds: 180`, `small_subs_max: 10000`, `small_age_days: 365`,
  `outlier_multiplier: 3`, `outlier_window_videos: 30`, `outlier_floor_views: {shorts:
  10000, longform: 2000}`, `window_days: 90`, `weights: {small_outlier_rate: 0.5,
  newcomer_view_share: 0.3, inverse_concentration: 0.2}`, `low_confidence_min_small: 5`,
  `low_confidence_cap: 0.4`, `top_n_concentration: 3`, `manual_hours_floor_per_month: 2`.
- `scoring/opportunity.py`, all pure, all taking the config dict:
  `is_small`, `channel_median(last_n)`, `outlier_videos`, `small_outlier_rate`,
  `newcomer_view_share`, `concentration`, `opportunity(...) -> (score, flags)`,
  `newcomer_monthly_views(...) -> (p25, p50, p75)` (linear interpolation, `statistics.quantiles`
  method `inclusive`).
- `scoring/money.py`: `calibration(own_rpm_usd, table, ref=("animals_nature","shorts"))`,
  `rpm_gbp(category, fmt, table, calibration, usd_gbp)` using the table's `mid`,
  `est_monthly_gbp(views, rpm_gbp)`.
- `scoring/effort.py`: `manual_hours_per_video(required_step_ids, steps, coverage, fmt)`
  honouring `automated` (0 h), `partial` (`manual_hours_override`), `manual`
  (`default_hours`, or `shorts_hours` when fmt is shorts and present), `floor_hours`,
  and `disqualifying_for_faceless` (returns `inf` → the niche is flagged `disqualified`);
  `manual_hours_per_month(per_video, videos_per_month)`.
- `scoring/final.py`: `score(est_monthly_gbp, manual_hours_per_month, floor)`.

### The worked example (put it in `tests/test_scoring.py` verbatim)

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

Assert every bold number to 3 significant figures. Also test: cap bites when opportunity
would be 0.6 with 4 small channels → 0.4; a `presenter` requirement → `inf` and
`disqualified`; floor of 2 h/month when manual hours are 0.5.

## Out of scope

- Reading from the DB or writing `niche_scores` (024).

## Acceptance criteria

- [ ] `pytest -q` passes; `tests/test_scoring.py` contains the table above as literals and
      asserts each bold value.
- [ ] `grep -rn "sqlite\|conn" src/ytscout/scoring/` finds nothing — scoring never touches
      the DB.
- [ ] Every numeric threshold in `scoring/*.py` is read from the config dict; a test that
      passes `outlier_multiplier: 2` changes the outlier set.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §6` is the spec; if you find the worked example disagrees with §6, the formula
in §6 wins and you fix the example and say so. The £1.04 is not a bug — Shorts RPM is
tiny, and that fact is exactly what the long-form column exists to show.
