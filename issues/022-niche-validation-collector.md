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
