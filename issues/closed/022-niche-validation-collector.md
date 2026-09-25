# 022 — `scout validate`: sample a niche's channels and videos within a unit budget

**Type**: AFK
**Blocked by**: 003, 021
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/scout/validate.py, tests/test_scout_validate.py, tests/fixtures/validate/
**Milestone**: M4

## Why

A proposed niche is a guess until real channels and real view counts sit behind it. This
is the most quota-hungry command in the project, so the budget discipline is the feature.

## Scope

- `ytscout scout validate (--niche ID | --all-proposed) [--max-units N] [--dry-run]`.
  Per niche (one format each — a niche row already has its format):
  1. Searches: the niche's 3 `example_search_queries` × 2 orders (`viewCount`, `date`),
     `published_after = now − 365 d`, `max_results = 50`, and `video_duration = short` for
     shorts / `medium` for long-form (the API's `short` is < 4 min — close enough to the
     180 s rule; the videos.list duration decides `is_short` later). **≤ 6 searches per
     niche** (600 units). Stop early if the first 4 already yielded ≥ 80 distinct channels.
  2. Distinct channels from the hits, capped at **80** by hit count. `channels(ids)`.
     `is_small = subs < small_subs_max (10,000) AND created_at > now − small_age_days (365)`.
  3. Per channel: uploads playlist, **one page** of `playlist_items` (50), then
     `videos(ids)` for the newest 30 → upsert videos + snapshots. ≈ 2 units per channel.
  4. Write `niche_channels(niche_id, channel_id, is_small)`; set `niches.status =
     'validated'`, `validated_at`.
  Typical cost ≈ 600 + 2 + 160 ≈ 800 units per niche; print the actual.
- `--all-proposed` processes niches in `created_at` order and stops cleanly on
  `QuotaExhausted` (exit 3) with the finished niches marked and the current one left
  `proposed` (its partial rows are harmless — snapshots are append-only).
- Channels that are already tracked as competitors are still sampled (they belong to
  the niche too) but never have their `role`/`status` changed.
- `--dry-run` prints per-niche planned searches and the unit estimate.
- Fixtures: one niche, 6 search responses with overlapping channels (12 distinct), a
  channels batch with 4 small / 8 big by the rule (include one channel that is small by
  subs but old, and one young but big), playlist pages and video batches.
- Tests: `is_small` on all four combinations; channel cap; search early-stop; unit total
  for the fixture run equals the hand-computed number; `--all-proposed` marks the
  finished niche and leaves the interrupted one `proposed` when the fake ledger raises.

## Out of scope

- Scoring (023/024). Snowball (026). Real runs (027).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_scout_validate.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout scout validate --all-proposed --dry-run` prints
      the plan for every proposed niche with a per-niche unit estimate ≤ 900.
- [ ] A test asserts the exact unit total for the fixture niche.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §5.3, §8.1`. No real API call in this session. Lifecycle becomes
`proposed → validated → scored → tracking | shelved`; update `§5.4` in the Outcome.

## Outcome (closed 2026-09-25)

Delivered in commit 49eec72 (plus this close-out). No real API units spent.

- `ytscout scout validate (--niche ID | --all-proposed) [--max-units N] [--dry-run]` in
  `src/ytscout/scout/validate.py`, wired in `cli.py` (no longer a stub). Flow as scoped:
  ≤ 6 searches (query-major: q1 viewCount, q1 date, q2 ...; `videoDuration` short/medium;
  `publishedAfter` now − 365 d; 50 results; `relevanceLanguage=en`, which is free), early
  stop after 4 when they already found 80 distinct channels, channels ranked by hit count
  (ties keep first-seen order) and capped at 80, then one uploads page + one `videos.list`
  for the newest 30 per channel. `niche_channels` written; `niches.status='validated'` and
  `validated_at` set (new migration **0006**, column `niches.validated_at`).
- **Worst case is 762 units per niche** (600 + 2 + 160), not the design's ~1k. Dry-run on
  the real DB: all 35 proposed niches print `estimate <= 762 units`; 6 would fit in the
  5,285 units left today.
- **Decision beyond the Scope: headroom check.** A niche is started only if its worst case
  fits under both `--max-units` and today's cap; otherwise the run stops with exit 3
  before spending anything on it. Without this, a run with `--max-units 3000` would sink
  600 search units into a fourth niche it could not finish. A Google `quotaExceeded`
  mid-niche still stops cleanly: finished niches stay `validated`, the current one stays
  `proposed`. Consequence for 027: `--max-units` below 762 validates nothing.
- `is_small` is NULL (not false) when subscribers are hidden or the creation date is
  missing, so 023 must treat NULL as "unknown", not "big".
- Thresholds live in `config/scoring.yaml` under `niche_validation:` (small_subs_max,
  small_age_days, lookback_days, max_channels, early_stop_after_searches,
  videos_per_channel, language), parsed by `scoring.validation_config`.
- `--niche ID` revalidates a `proposed` or `validated` niche and refuses `track`/`shelve`
  so a decision is never undone. New channels get role `niche_sample`; existing competitor
  rows keep role and status (tested with an approved competitor in the fixture).
- Fixture niche (`tests/fixtures/validate/`): 6 searches, 12 channels (4 small / 8 big,
  with small-by-subs-but-old, young-but-big, exactly-at-cap and exactly-365-days edge
  cases), one 35-upload channel (30 fetched) and one empty playlist. Hand-computed total
  600 + 1 + 12 + 11 = **624 units**, asserted. 25 tests in `tests/test_scout_validate.py`;
  suite 374 passed; ruff check and format clean.
- §5.4 updated in DESIGN.md: lifecycle `proposed → validated → scored → tracking | shelved`,
  noting that the stored decision values are the dashboard's `track`/`shelve`. §5.3 notes
  the as-built 762-unit ceiling.
- Shorts vs long-form is still the plain `duration ≤ shorts_max_seconds` rule from
  `collect/walk.py`; the "channel's own format mix" refinement in §5.3 stays with scoring.
