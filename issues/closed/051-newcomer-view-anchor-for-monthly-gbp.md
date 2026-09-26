# 051 — Decide what the £/month estimate should anchor on

**Type**: AFK
**Blocked by**: 049
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: DESIGN.md §6.3, config/scoring.yaml, src/ytscout/scoring/money.py, src/ytscout/scoring/niches.py, src/ytscout/dashboard/
**Milestone**: M5

## Why

027 scored five niches. `est_monthly_gbp` is the median small channel's monthly views
times the RPM tier, per `DESIGN.md §6.3`. The median small channel in every Shorts niche
gets about 2,000 views a month, so every Shorts niche rounds to £0 and the ranking is
decided entirely by which long-form RPM tier a niche sits in. The reference niche, the
channel's own, scored 0.004. That is the formula working as written, but it makes the
score useless for comparing Shorts niches against each other, which is half the point.

## Scope

- **Decided 2026-09-26: newcomer monthly views = the 75th percentile of monthly views
  over small channels that published at least one video in the last `window_days`.** "A
  good newcomer", not the typical one. Put the percentile (`newcomer_views_percentile:
  75`) and the active-in-window rule in `config/scoring.yaml` under `niche_scoring`, as
  named thresholds, not in code. Keep reporting p25 and p75 of the same active set as the
  band; the £ band label on the dashboard must say which percentile the estimate is.
- Runs after 049 (Blocked by), because polluted samples change the percentiles.
- Update `DESIGN.md §6.3` and the dashboard's £ band label to match.
- Re-score the 027 niches from stored data and put both rankings in the Outcome. No API
  units.

## Out of scope

- Changing the RPM tiers or the calibration (050).
- Adding non-AdSense revenue.

## Acceptance criteria

- [ ] `ytscout score` ranks the five 027 niches with the new anchor and at least two
      Shorts niches are distinguishable from each other.
- [ ] `ytscout scout sensitivity` (028) still runs.
- [ ] Outcome: the anchor chosen, why, and the before/after ranking.

## Notes

The p25/p50/p75 columns are already in `niche_scores`, so the dashboard can show all
three whatever the score uses. Issue 028 is the sensitivity harness for this kind of
change.

## Outcome (closed 2026-09-27)

**Anchor chosen**: the 75th percentile of monthly views over small channels that published
at least one in-format video in the last `window_days`, as the Scope decided. Config:
`niche_scoring.newcomer_views_percentile: 75` and `niche_scoring.newcomer_min_window_videos: 1`
in `config/scoring.yaml`, both validated (`percentile` 0–100, `min videos` ≥ 0; setting it
to 0 restores the old "every small channel" set). Why: the median of every small channel
put each Shorts niche at ~2,000 views a month and £0, and dormant small channels (0 window
views) dragged the set down further. p75 of the active set is "a good newcomer publishing
on schedule", which is what the pipeline would be.

**Delivered**
- `scoring/opportunity.py`: `active_small_channels`, `newcomer_anchor_views` (the configured
  percentile); `newcomer_monthly_views` (p25/p50/p75, the band) now runs over the active set.
  Quantiles go through `scoring.metrics.percentile` (same linear interpolation as before).
- `scout/score.py`: `est_monthly_gbp` = anchor views / 1000 × `rpm_gbp`. New flag
  `no_active_small_channels` when small channels exist but none published in the window
  (views NULL, £0); `no_small_channels` keeps its meaning.
- Dashboard: the £ column header reads "Est £/month p75 (band p25–p75)" with the
  percentile taken from `scoring.yaml` ("(anchor)" when the file does not load); the cell
  shows the stored estimate and the p25–p75 band. Checked by building the dashboard
  against the real DB and by a test asserting the header.
- `DESIGN.md §6.2/§6.3` rewritten to the new rule (the issue wins over the old median text).
- Tests: hand-worked anchor (023 example: p75 = 26,667 views → £2.08, score 0.116, was
  £1.04 / 0.0578), inactive channel excluded, the rule switched off, percentile follows
  config, config rejects out-of-range values. 607 passed.
- `docs/sensitivity.md` regenerated under the new anchor.

**Re-score of the five 027 niches** (stored data, 0 API units, 0 `claude -p` calls).
`ytscout score --niches` scored the three tracked ones (1, 3, 33) and appended rows;
14 and 20 are shelved, so the default score run skips them. All five were ranked with the
same `score_niche` via `scout sensitivity --niches 1,3,14,20,33` (default cell, no DB
writes) and a read-only call; the default cell matches the `score` run for 1, 3, 33.

Before (p50 of all small channels, 2026-09-26 rows):

| # | id | niche | fmt | £/mo | h/mo | score |
|---|----|-------|-----|------|------|-------|
| 1 | 3 | One-animal deep dives | longform | 4.88 | 7.6 | 0.642 |
| 2 | 33 | Aviation incidents explained | longform | 4.41 | 7.6 | 0.580 |
| 3 | 1 | Top 5 dangerous animals (own) | shorts | 0.09 | 22 | 0.0041 |
| 4 | 20 | Prehistoric giants countdown | shorts | 0.08 | 22 | 0.0035 |
| 5 | 14 | Engineering disasters countdown | shorts | 0.09 | 37 | 0.0024 |

After (p75 of active small channels):

| # | id | niche | fmt | p25 / p50 / p75 views | £/mo | h/mo | score |
|---|----|-------|-----|-----------------------|------|------|-------|
| 1 | 3 | One-animal deep dives | longform | 747 / 7,436 / 28,724 | 112 | 7.6 | 14.7 |
| 2 | 33 | Aviation incidents explained | longform | 268 / 1,136 / 12,432 | 77.6 | 7.6 | 10.2 |
| 3 | 20 | Prehistoric giants countdown | shorts | 105 / 4,352 / 10,754 | 0.42 | 22 | 0.019 |
| 4 | 14 | Engineering disasters countdown | shorts | 424 / 4,934 / 7,132 | 0.39 | 37 | 0.0105 |
| 5 | 1 | Top 5 dangerous animals (own) | shorts | 610 / 2,338 / 5,234 | 0.20 | 22 | 0.0093 |

The Shorts niches now separate (0.019 vs 0.0105 vs 0.0093, a 2× spread) and the own niche
moves from first to last Shorts. `scout sensitivity` still runs; the top-3 holds in all 27
cells, and 14/1 swap places in 3 of them.

**What this does not fix, for whoever is next**
- Shorts are still pennies against long-form pounds: at a ~£0.04 Shorts RPM even a p75
  newcomer earns under £1/month, so the cross-format ranking is still decided by the RPM
  tiers. That is 050/RPM territory (out of scope here), and alongside the pinned goal it
  argues for months-to-monetisation (054) mattering more than £/month for Shorts.
- p75 is more sensitive to the small set's size than p50 was: niche 3 swings
  10.9–129 across the grid (small_age_days=180 leaves a few hot channels). Worth a look
  when 028's grid is next read.
- The dashboard still shows the 2026-09-26 p50-era rows for shelved niches 14 and 20
  (only tracked/scored niches are re-scored by default). The header labels every row with
  the current config percentile, so those two rows are mislabelled until they are re-scored.
- Not checked by eye: how the new header reads on the dashboard (verified only by
  building it and grepping the HTML).
