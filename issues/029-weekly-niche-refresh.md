# 029 — `collect --niches`: cheap weekly refresh of tracked niches, wired into the weekly run

**Type**: AFK
**Blocked by**: 013, 022
**Add dirs**: none
**Covers**: src/ytscout/collect/niches.py, scripts/run_weekly.ps1, tests/test_collect_niches.py
**Milestone**: M4

## Why

A tracked niche is a 12-month trend line, and a trend line needs weekly points. This adds
them for ~40 units a niche with no searches at all.

## Scope

- `ytscout collect --niches [--max-units N] [--dry-run]`: for every niche with
  `status='tracking'`, for every channel in `niche_channels`:
  1. `channels(ids)` in batches → snapshot (1 unit per 50 channels).
  2. `playlist_items` first page only; `videos(ids)` for videos new or ≤ 90 days old →
     upsert + snapshot.
  No `search.list` ever. Print units per niche and total.
- Then re-run the niche's scoring (call 024's `score` for that niche) so `niche_scores`
  gets its weekly row and the dashboard's trend arrow has data.
- `scripts/run_weekly.ps1`: the `collect --niches --max-units 8000` step (13 left a slot)
  runs before `score`; confirm the order in `-DryRun`.
- `QuotaExhausted` → exit 3 after committing finished niches (032 adds resume).
- Tests: fixture with one tracking niche of 3 channels → expected calls and unit total;
  a `shelved` niche is untouched; a new `niche_scores` row appears after the run.

## Out of scope

- Re-validating (new searches) — that is a manual `scout validate` when a niche's sample
  looks stale.

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_collect_niches.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --niches --dry-run` prints the plan and
      a per-niche estimate ≤ 60 units for a 30-channel niche.
- [ ] `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -DryRun`
      shows `collect --niches` before `score`.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §5.4, §8.1` ("Refresh ~30 tracked niches ≈ 1,200 units").
