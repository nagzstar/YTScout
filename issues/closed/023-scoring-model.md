# 023 — The scoring model as pure functions, tested against a hand-worked example

**Type**: AFK
**Blocked by**: 002
**Add dirs**: none
**Model**: claude-fable-5-1 high
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

## Outcome (closed 2026-09-25)

Delivered `src/ytscout/scoring/{types,opportunity,money,effort,final}.py`, a
`niche_scoring:` section in `config/scoring.yaml` with `scoring.niche_scoring_config`
to validate it, and `tests/test_scoring.py` (33 tests) carrying the worked example
verbatim. Every bold number in the table above is asserted to 3 significant figures and
matches DESIGN.md §6 as written; the example needed no correction. Suite: 407 passed;
ruff format and check clean; `grep -rn "sqlite\|conn" src/ytscout/scoring/` finds
nothing (also a test). Commit c1ed302.

Decisions, and why:

- **Config shape.** The keys the Scope lists sit under `niche_scoring:` rather than at the
  top level, next to the `discovery:`, `competitor_metrics:` and `niche_validation:`
  sections earlier issues made. `niche_scoring_config(doc)` returns a plain dict (the
  section plus the top-level `shorts_max_seconds`) and that dict is the `cfg` every
  function in `opportunity.py` takes, so a test can pass `{**CFG, "outlier_multiplier": 2}`
  and watch the outlier set change (`test_outlier_multiplier_from_config_changes_the_outlier_set`).
  `small_subs_max`/`small_age_days` repeat `niche_validation` and `outlier_multiplier`/
  `outlier_window_videos` repeat `competitor_metrics` on purpose: each section is read by
  a different command; `test_yaml_matches_worked_example_config` fails if the pairs drift.
  `videos_per_month: {shorts: 20, longform: 4}` (§6.4) was added to the section because
  024 needs it and every other number was already there.
- **Format first.** A niche is `format × topic`, so every view count, median and outlier
  is over the channel's videos in the sample's format (`duration_s ≤ shorts_max_seconds`
  is a Short; no duration means no format). The median uses the channel's newest
  `outlier_window_videos` in-format videos whether or not they fall inside the window;
  only videos inside the window can be outliers or contribute views.
- **Unknown is not big.** `is_small` returns `None` when subs or creation date are
  missing (022 stores NULL for hidden subscriber counts). Such channels count in the
  niche's totals and in concentration, never in the small set.
- **Empty cases.** `small_outlier_rate` and `newcomer_view_share` are 0 with nothing to
  divide; `concentration` is 1.0 for a niche with no views (no evidence of room, so the
  inverse term contributes nothing); `newcomer_monthly_views` returns `None` with no small
  channel and `(v, v, v)` with one, since `statistics.quantiles` needs two points.
- **Money never imports the loader.** `scout.propose.load_rpm_tiers` pulls in `sqlite3`
  and the store, so `money.py` accepts any object with `row(category, fmt).mid` (a
  `Protocol`); the real `RpmTable` satisfies it, the test uses a five-line fake.
  `calibration` is 1.0 when the own RPM is `None` or 0, and `money.UNCALIBRATED` names
  the badge for the dashboard. Note the real table's `animals_nature.shorts.mid` is 0.05,
  not the example's 0.08; the example's table is a literal in the test.
- **Effort reuses the audit.** `manual_hours_per_video` sums `audit.effective_hours` over
  the required steps and returns `(hours, flags)`: `inf` and `disqualified` when a
  required step is `disqualifying_for_faceless`; a required step with no coverage entry
  is costed manual; an unknown step id raises `KeyError`. A format outside
  `formats_supported` is costed fully manual, as the audit does. `final.score` returns 0
  for `inf` hours so a disqualified niche ranks last rather than crashing.
- `opportunity` and `manual_hours_per_video` both return `(value, flags)` with the flag
  names as module constants (`LOW_CONFIDENCE`, `DISQUALIFIED`, `UNCALIBRATED`) for 024
  to store.

Unverified: nothing needed a human.

For 024: build `NicheSample` from `channels`/`videos` rows (latest snapshot views,
`published_at`, `duration_s`, subs and creation date, both nullable), the `RpmTable`
via `scout.propose.load_rpm_tiers`, steps and coverage via `audit.load_steps` /
`load_coverage`, the own RPM in USD from the own-channel Analytics rows (calibration =
1.0 when absent, store the `uncalibrated` flag), `usd_gbp` from settings, and the
niche's required step ids and topic category from its stored brainstorm output.
`window_days` is 90, so a niche validated with 022's 365-day lookback has plenty of
history but only the last 90 days count.
