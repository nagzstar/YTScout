# 054 — Months to Partner Programme monetisation, per niche × format

**Type**: AFK
**Blocked by**: 051
**Add dirs**: none
**Model**: claude-fable-5-1 high
**Covers**: src/ytscout/scoring/ypp.py, src/ytscout/scoring/niches.py, config/scoring.yaml, src/ytscout/dashboard/, DESIGN.md §6.6, tests/test_ypp.py
**Milestone**: M4

## Why

`DESIGN.md §2` decision 17: the goal is a monetised channel as fast as possible, in
whichever format gets there first. Today's score assumes AdSense is already flowing.
A newcomer earns £0 until the Partner Programme accepts the channel, and the entry
thresholds differ by an order of magnitude between formats, so the ranking hides the one
number Nagz most needs: how many months a good newcomer in this niche takes to qualify.

## Scope

- New pure module `scoring/ypp.py`: `months_to_ypp(niche_sample, config) -> YppEstimate`
  with fields `months_subs`, `months_views`, `months` (the max), `reachable: bool`,
  `flags`. No DB import; takes the same plain dataclasses as `opportunity.py`.
- Thresholds in `config/scoring.yaml` under `niche_scoring.ypp`, editable, each with a
  comment naming the YouTube Help page they came from and a `last_reviewed`:
  `subs_min: 1000`; long-form `watch_hours_12mo: 4000`; Shorts `views_90d: 10000000`.
  Verify the current figures against YouTube Help before writing them; if they have
  changed, use the current ones and say so in the Outcome.
- Newcomer rates come from the 051 anchor (p75 of active small channels):
  - `subs_per_month` = p75 over active small channels of `subs ÷ channel age in months`;
    `months_subs = subs_min ÷ subs_per_month`.
  - Shorts: `months_views` = months until a rolling 90-day window holds `views_90d` at
    the newcomer's monthly rate, i.e. `reachable` only when `monthly_views × 3 ≥ views_90d`
    — otherwise `reachable = false`, `months = None`, flag `ypp_unreachable_at_rate`.
  - Long-form: `watch_hours_per_month = monthly_views × median video duration of the
    sample × retention_share ÷ 3600`, `retention_share` in config (default 0.4, commented
    as an assumption); `reachable` when `watch_hours_per_month × 12 ≥ watch_hours_12mo`.
  - Assume the newcomer starts at zero and grows linearly to the p75 rate over
    `ramp_months` (config, default 3); say the formula in a docstring and in §6.6.
- `score_niches` stores the estimate on `niche_scores` (new numbered migration: columns
  `months_to_ypp REAL NULL`, `ypp_reachable INTEGER`, `ypp_flags_json`).
- Dashboard Niches table: new column "months to YPP", **default sort ascending by it**
  with unreachable rows last; `score` remains a sortable column. The £ band tooltip says
  "after monetisation".
- `DESIGN.md`: add §6.6 with the formulas; update the §6.5 dashboard paragraph.
- Tests: hand-worked example per format (one reachable, one unreachable), the migration,
  the sort order in the rendered HTML. No API units, no real `claude -p`.

## Out of scope

- Changing `score` itself. Modelling the 500-sub fan-funding tier (no ad revenue).
- Subscriber conversion modelling beyond subs ÷ age.

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_ypp.py`.
- [ ] `ytscout score` on the real DB exits 0 and every scored niche has `months_to_ypp`
      or `ypp_reachable = 0`.
- [ ] `ytscout dashboard` renders the column and sorts by it; the 027 niches' values are
      in the Outcome with one line on whether they look plausible.

## Notes

The 027 reference niche (Shorts, ~2,000 median views a month before 051) should come out
unreachable or far away; that is the expected result, not a bug. `DESIGN.md §6.2–6.3`
for the anchor; issue 051's Outcome for the exact percentile rule.

## Outcome (closed 2026-09-27)

**Thresholds verified** against YouTube Help "YouTube Partner Program overview &
eligibility" (https://support.google.com/youtube/answer/72851) on 2026-09-27: 1,000
subscribers, plus 4,000 public watch hours in the last 12 months (long-form) or 10 million
public Shorts views in the last 90 days. They match the issue; nothing changed. They sit in
`config/scoring.yaml` under `niche_scoring.ypp` with `source` and `last_reviewed`.

**Delivered**
- `scoring/ypp.py`: `months_to_ypp(sample, cfg) -> YppEstimate` (`months_subs`,
  `months_views`, `months`, `reachable`, `flags`, plus the rates it used). Pure, no DB; the
  full model is in the module docstring and `DESIGN.md §6.6`.
- Newcomer rates come from the 051 anchor set (active small channels) at the configured
  percentile (75): `subs_per_month` = p75 of `subs ÷ age in months`; monthly views =
  `newcomer_anchor_views`; long-form watch hours = views × median duration of the active
  small channels' window videos × `retention_share` ÷ 3600.
- The ramp (zero to the anchor rate over `ramp_months`) applies to all three months
  figures, found by bisection on the cumulative / rolling-window totals to 1e-4 month.
  Decision: the issue gives both the plain `subs_min ÷ subs_per_month` and the ramp; the
  ramp wins, and with `ramp_months: 0` the plain division comes back (tested).
- Migration `0013`: `months_to_ypp REAL`, `ypp_reachable INTEGER`, `ypp_flags_json TEXT`
  on `niche_scores`; `repo.add_niche_score` takes `ypp_flags`. `score_niche` stores all
  three; `format_table` shows a "YPP mo" column ("never" when unreachable).
- Dashboard: "Months to YPP" column, default order months ascending with unreachable and
  unknown rows last, then score, then id (`build._niche_order`); both that header and
  Score are click-sortable in JS (each niche row moves with its sample row; "never" and
  unknown rows stay last either way); the £ header tooltip now starts "Estimate after
  monetisation"; the YPP flags render as badges with reader-facing labels.
- Config: `niche_scoring.ypp` validated (`subs_min` int ≥ 1, the two thresholds and
  `age_floor_months` > 0, `retention_share` 0–1, `ramp_months` ≥ 0). Two extra keys
  beyond the issue's list: `age_floor_months: 1` (a days-old channel with a few hundred
  subs would otherwise set the subs pace) and `retention_share: 0.4` (an assumption,
  commented as such).
- `DESIGN.md`: new §6.6 with the formulas, §6.5 dashboard paragraph and §10 data model
  updated.
- Tests: `tests/test_ypp.py` (hand-worked Shorts reachable 4.83 mo / unreachable,
  long-form reachable 4.83 mo with subs as the wall / unreachable, the 023 example, zero
  ramp, zero subs, no durations, config validation); migration round-trip in
  `test_scout_score.py`; rendered sort order, data attributes and badges in
  `test_dashboard.py`. 631 passed, ruff clean. No API units, no real `claude -p`.

**Real DB** (`ytscout score`, exit 0, 0 API units): the three tracked 027 niches were
re-scored; the two shelved ones (14, 20) are not scorable by design, so their latest rows
carry NULL YPP columns and the dashboard shows "–" (unknown), sorted last. The values
below for 14 and 20 come from a read-only `months_to_ypp` call on the same sample.

| id | niche | fmt | subs/mo | views/mo | wh/mo | months_subs | months_views | months to YPP |
|----|-------|-----|---------|----------|-------|-------------|--------------|---------------|
| 3  | One-animal deep dives | longform | 149 | 28,724 | 1,805 | 8.2 | 3.7 | **8.2** |
| 33 | Aviation incidents explained | longform | 98 | 12,432 | 807 | 11.8 | 6.5 | **11.8** |
| 20 | Prehistoric giants countdown | shorts | 224 | 10,754 | – | 6.0 | never | never |
| 14 | Engineering disasters countdown | shorts | 38 | 7,132 | – | 27.8 | never | never |
| 1  | Top 5 dangerous animals (own) | shorts | 26 | 5,234 | – | 40.4 | never | never |

Plausible: for long-form, subscribers are the wall (watch hours arrive in 4–7 months at
~10-minute videos), which is what faceless long-form channels report; for Shorts, a p75
newcomer at 5–11k views a month is three orders of magnitude short of 10M in 90 days, so
"never" is the honest answer, as the issue's Notes expected. The dashboard's default
order is therefore 3, 33 (long-form) and, for Shorts, all unreachable ranked by score.

**Not verified by eye**: how the new column, the sort arrows and the "never" cells read
in a browser, and that clicking a header re-sorts. Checked by building the real dashboard
and grepping the HTML for the header's `aria-sort="ascending"`, the row order and the
`data-months-ypp` attributes, and by the rendered-HTML test.

**For whoever is next**
- Re-scoring a shelved niche needs it un-shelved first; until then its row is "unknown"
  on the dashboard, not "never".
- `retention_share` is a guess. Once the own channel has long-form Analytics, replace it
  with a measured average-view-percentage (Analytics `averageViewPercentage`).
- The subs rate is `subs ÷ age`, so a channel that grew fast then stalled looks the same
  as a steady grower (the issue left conversion modelling out of scope).
- `docs/sensitivity.md` was not regenerated: `score` is unchanged, and the sensitivity
  grid does not sweep the YPP settings.
