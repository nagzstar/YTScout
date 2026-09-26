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

## Outcome (closed 2026-09-26)

**Delivered**: `score --competitors` now runs immediately after `collect --niches` and before `analyse --summaries`, so the competitor comparison packet has current channel metrics from the same week's collection.

**Changes**:
- `scripts/run_weekly.ps1`: Inserted `score --competitors` step (line 63) in the `$Steps` array between collectors and analyses
- `scripts/run_weekly.ps1`: Updated `.DESCRIPTION` to document the step order and explain that `score --competitors` is cheap and can run twice a week (issue 043)
- `tests/test_scripts.py`: Updated `EXPECTED_STEPS` list to include `"score --competitors"` in the correct position

**Verification**:
- `powershell -File scripts\run_weekly.ps1 -DryRun` produces 10 steps with `score --competitors` between `collect --niches` and `analyse --summaries` ✓
- `pytest -q` passes all 547 tests ✓
- `ruff format` and `ruff check` pass ✓
- `python -m ytscout --help` runs successfully ✓

**Notes for next issue**: The step order is now correct. The competitor packet for `analyse --competitors` will have current metrics from the same week, not from the previous week. This fixes the caveat seen in analysis ID 2.
