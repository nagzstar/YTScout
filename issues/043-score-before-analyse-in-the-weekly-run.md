# 043 — Score competitors before the comparison packet is built

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-haiku-4-5-20251001 medium
**Covers**: scripts/run_weekly.ps1, the test that asserts the `-DryRun` step order, DESIGN.md §9
**Milestone**: M5

## Why

`run_weekly.ps1` runs `analyse --competitors` before `score --all`. The competitor packet
carries each channel's *latest* `channel_metrics` row, so the comparison is always built
on the previous week's metrics, and on the first week on none at all: the 2026-09-25 01:04
analysis (`competitor_analyses.id 2`) opens with the caveat "Metrics are null for every
channel, so views_per_sub, upload rate and title features could not be used."

## Scope

- Insert `score --competitors` immediately before `analyse --summaries` (after the
  collectors). Keep `score --all` at the end so niches refreshed by `collect --niches` are
  still scored after the analyses and the dashboard sees everything.
- `score --competitors` is cheap and deterministic, so running it twice a week is fine;
  say so in the script's header comment.
- Update the `-DryRun` order test and the step table in the script's `.DESCRIPTION`.

## Out of scope

- Making `analyse` compute metrics itself.

## Acceptance criteria

- [ ] `powershell -File scripts\run_weekly.ps1 -DryRun` prints `score --competitors`
      between `collect --niches` and `analyse --summaries`, and a test asserts that order.
- [ ] `pytest -q` passes.

## Notes

`src/ytscout/packets.py` `competitor_packet` (metrics per channel come from
`repo.latest_channel_metrics`). `issues/closed/013-*.md` for the original step order.
