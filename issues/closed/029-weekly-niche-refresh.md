# 029 — `collect --niches`: cheap weekly refresh of tracked niches, wired into the weekly run

**Type**: AFK
**Blocked by**: 013, 022
**Add dirs**: none
**Model**: claude-opus-5-5 medium
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

## Outcome (closed 2026-09-25)

Delivered `ytscout collect --niches [--max-units N | --dry-run]` in
`src/ytscout/collect/niches.py`, wired into `cmd_collect`. For each niche with status
`track` (the value the dashboard writes) or `tracking` (DESIGN.md's name), oldest first:
`channels.list` in batches of 50 with one snapshot per channel, one `playlistItems.list`
page per channel (never page 2), then `videos.list` for the page's videos that are new to
the DB or ≤ 90 days old. It prints units per niche and the run total. There is no
`search.list` call anywhere in the path. Then it re-scores only the niches it finished,
through a new `niche_ids` filter on `scout.score.score_niches`, so each gets its weekly
`niche_scores` row. A `track` status survives the re-score.

Decisions:
- **`videos.list` ids are pooled across the niche's channels**, 50 per call. A 30-channel
  niche costs `1 + 30 + ceil(recent / 50)`, about 32–40 units. That matches the issue's
  "~40 units". Batching per channel would have cost 61 and failed the ≤ 60 criterion.
- A channel in two tracked niches is refreshed once per run, under the first niche. The
  report shows it as `shared`.
- On a quota stop, every finished batch is already committed. Finished niches are
  re-scored and the run exits 3. A niche cut off mid-refresh is not scored.
- The dry run plans one stand-in video per channel. It therefore shows one pooled
  `videos.list` call per niche, and the printed note says so.
- `scripts/run_weekly.ps1` needed no change. Issue 013 had already put
  `collect --niches --max-units 8000` before `score --all`. The `-DryRun` output confirms
  the order.

Checked by running:
- `pytest -q`: 468 passed, including the 7 tests in `tests/test_collect_niches.py`.
  Those tests cover:
  - the call list and the 5-unit total for a tracked niche of 3 channels;
  - a shelved niche left untouched;
  - a shared channel refreshed once;
  - a quota stop that keeps its batches;
  - a new `niche_scores` row after the CLI run;
  - exit 3 without scoring;
  - a 30-channel niche dry run planning 32 units.
- `ruff check .` and `ruff format --check .` are clean.
- `run_weekly.ps1 -DryRun` works.
- `collect --niches --dry-run` against the real DB prints the plan. That DB has no
  tracked niches yet, so the real run shows no per-niche line. The ≤ 60-unit estimate for
  a 30-channel niche is proven by the CLI dry-run test, not by live data.

Not checked: a real API run, because the issue grants no units.

For later issues:
- A tracked niche gets two `niche_scores` rows per weekly run: one from
  `collect --niches` and one from `score --all`. The dashboard trend compares against a
  row ≥ 28 days older, so it is unaffected.
- A channel that is both a competitor and in a tracked niche gets two channel snapshots
  a week. That is harmless, because the table is append-only.
- Resume is 032's job.
