# 051 — Decide what the £/month estimate should anchor on

**Type**: Active
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

- Nagz decides, with Claude alongside, what "a newcomer's monthly views" should mean.
  Candidates: p75 of small channels ("a good newcomer"), the median of small channels
  that have at least one outlier, or the median of small channels active in the last 90
  days rather than all small channels. Whatever is chosen goes in `config/scoring.yaml`
  as a named threshold, not in code.
- Re-run this after 049, because polluted samples change the percentiles.
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
