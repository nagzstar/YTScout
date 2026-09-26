# 050 — Verify why the money model is uncalibrated

**Type**: AFK
**Blocked by**: 027
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/collect/own.py, src/ytscout/youtube/analytics.py, src/ytscout/scoring/money.py, src/ytscout/cli.py
**Milestone**: M5

## Why

Every niche score from 027 carries `uncalibrated`. The own channel has analytics windows
for 11 videos, 8 of them with over 1,000 views, but none of the windows ending in the last
90 days carry an RPM, so `money.calibration` returns 1.0. Either the channel is not yet
monetised, in which case the flag is correct and the dashboard should say why, or it is
monetised and the Analytics collector's monetary query is not returning data, which is a
bug that silently zeroes the calibration point the whole money model rests on.

## Scope

- **Decided 2026-09-26: the channel is not yet in the Partner Programme.** So the
  `uncalibrated` flag is correct and the "not monetised" branch below is the whole job.
  Do not print revenue or RPM figures in the transcript or the Outcome; report presence
  or absence only.
- Not needed now, but leave the code path intact: when the channel is monetised the
  Analytics collector's monetary query must fill `own_analytics.rpm_usd` and the flag must
  drop by itself. Add a test for that path too (fixture rows with RPM present).
- Make `ytscout doctor` and the dashboard badge say "uncalibrated: no
  monetised views yet" rather than the bare word, so the next reader does not chase a bug.
- Either way, add a test in `tests/` for the calibration path with fixture rows that have
  views but no RPM.

## Out of scope

- Changing the RPM tier table.

## Acceptance criteria

- [ ] After the fix, `ytscout score` keeps the `uncalibrated` flag and the dashboard and
      `ytscout doctor` say "uncalibrated: no monetised views yet".
- [ ] Outcome: monetised or not, and what changed.

## Notes

`DESIGN.md §6.3` is the money model; §6.3 as-built note says calibration uses the median
RPM over own Shorts with at least 1,000 views in a window ending within 90 days.
