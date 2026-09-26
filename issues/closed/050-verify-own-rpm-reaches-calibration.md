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

## Outcome (closed 2026-09-27)

**Not monetised.** The channel is not yet in the Partner Programme (decided 2026-09-26), so
own Analytics rows carry views but no RPM and `uncalibrated` is the correct flag. No bug in
the Analytics collector: its monetary query already writes `own_analytics.rpm_usd` whenever
`estimatedRevenue` comes back, and the flag drops by itself once it does. `doctor` against
the real DB reports the calibration as absent; no revenue or RPM figure was printed.

What changed:

- `scoring/money.py`: `UNCALIBRATED_LABEL = "uncalibrated: no monetised views yet"` and
  `flag_label()`. The stored flag stays the bare `uncalibrated`, so existing
  `niche_scores` rows, the sensitivity check and tests are unaffected; only display text
  changes.
- Dashboard: niche badges render through a `flag_label` Jinja filter. Rebuilt against the
  real DB to a scratch file: 3 badges read "uncalibrated: no monetised views yet".
- `ytscout doctor`: new info row "money model calibration" — either that label or
  "calibrated from own Shorts RPM (value not shown)". Presence only. It imports
  `scout.score` lazily (that module pulls in the collectors, which plain doctor must not
  load) and skips when `config/scoring.yaml` is missing. Checked with `doctor --offline`.
- `ytscout score --niches`: a `note: uncalibrated: no monetised views yet …` line under the
  table when any row carries the flag. Ran for real; the flag is kept on every Shorts row.
- `tests/test_calibration.py`: collector → calibration end to end with a fake Analytics
  transport, for views with no revenue (`None` and `0.0`: uncalibrated) and views with
  revenue (RPM filled, calibration = RPM ÷ table mid); plus score/format_table with and
  without the flag. Doctor and dashboard tests cover the new text.

Unverified: how the longer badge looks on the dashboard by eye (content checked, layout not).
Zero revenue is treated the same as none, which matches `money.calibration`'s existing
`<= 0` rule. 0 API units and 0 `claude -p` calls spent.
