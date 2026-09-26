# 050 — Verify why the money model is uncalibrated

**Type**: Active
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

- Nagz says whether the channel is in the Partner Programme. Do not print revenue or RPM
  figures in the transcript or the Outcome; report presence or absence only.
- If monetised: run the Analytics collector with `--dry-run` and then for real under
  `--max-units 10`, inspect which metrics the monetary query asks for and which come back,
  and fix the collector so `own_analytics.rpm_usd` fills. Check the token's scopes with
  `ytscout doctor` first.
- If not monetised: make `ytscout doctor` and the dashboard badge say "uncalibrated: no
  monetised views yet" rather than the bare word, so the next reader does not chase a bug.
- Either way, add a test in `tests/` for the calibration path with fixture rows that have
  views but no RPM.

## Out of scope

- Changing the RPM tier table.

## Acceptance criteria

- [ ] After the fix, `ytscout score` either drops the `uncalibrated` flag or the
      dashboard says why it is there.
- [ ] Outcome: monetised or not, and what changed.

## Notes

`DESIGN.md §6.3` is the money model; §6.3 as-built note says calibration uses the median
RPM over own Shorts with at least 1,000 views in a window ending within 90 days.
