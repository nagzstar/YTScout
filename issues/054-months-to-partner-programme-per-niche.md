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
