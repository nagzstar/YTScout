# 010 — `collect --competitors` + the §4.4 metrics, side by side in the dashboard

**Type**: AFK
**Blocked by**: 006, 007
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/collect/competitors.py, src/ytscout/scoring/metrics.py, src/ytscout/dashboard/templates/competitors.html.j2, tests/test_metrics.py, tests/test_collect_competitors.py
**Milestone**: M1

## Why

This is the competitor analyser's numbers half: what they post, how often, how it does,
next to ours. It is also the weekly refresh that keeps 12 months of history growing.

## Scope

- `ytscout collect --competitors [--max-units N] [--dry-run]`: for every channel with
  `status in ('approved','watch')` plus the own channel:
  1. `channels(ids)` in batches → snapshots.
  2. Walk `playlist_items` page by page (reuse `collect/walk.py`) and **stop** when a page's
     oldest video is already in the DB *and* older than 90 days.
  3. `videos(ids)` for every video that is new or published within 90 days → upsert +
     snapshot. Videos older than 90 days keep their last snapshot (they barely move —
     `DESIGN.md §8.1`).
  Budget guide: ~3 units per channel per week.
- `scoring/metrics.py`: `channel_metrics(videos_with_snapshots, *, window_days, fmt,
  now) -> dict`, pure, for `window_days in (90, 365)` and `fmt in ('shorts','longform')`:
  - `uploads_per_week`
  - `views_median`, `views_p25`, `views_p75`, `views_max` (latest snapshot per video)
  - `views_per_sub` (median views ÷ latest subs)
  - `outlier_count` and `outlier_ids`: views ≥ `outlier_multiplier` (3) × the channel's
    median over its last `outlier_window_videos` (30)
  - `length_buckets`: counts for `<30s, 30–60s, 60–180s, 3–8min, 8–20min, 20min+`
  - `title_features`: mean length, share with a number, share with `?`, share matching
    `top \d+`, share ALL-CAPS-word ≥ 1
  - `velocity`: for videos with ≥ 2 snapshots, median views at the first snapshot taken
    within 7 days of publish, and at the first within 30 days — `null` when no such
    snapshots exist yet (this fills in over weeks; document the approximation)
  - `share_of_tracked_views`: this channel's window views ÷ all tracked channels' window views
  Thresholds from `config/scoring.yaml` (`outlier_multiplier`, `outlier_window_videos`,
  `length_buckets`); the function takes them as arguments, never reads the file itself.
- `ytscout score --competitors` computes both windows × both formats for every tracked
  channel and writes `channel_metrics` rows (append; the dashboard reads the latest).
  Add it to the `score` subcommand now; 024 adds niches to the same command.
- Dashboard competitors section: a side-by-side table (own channel first, bold) with
  uploads/wk, median views, p25–p75, views/sub, outliers, top length bucket; a toggle
  90d/365d; and a monthly-views line chart per channel for the last 12 months built from
  `published_at` × latest views (an approximation — label it "views by publish month").
- Tests: hand-computed fixture of 2 channels × 8 videos — assert median, p25, p75,
  `uploads_per_week`, outlier ids, bucket counts, title features exactly; the walk stops
  at the right page; `share_of_tracked_views` sums to 1 across channels.

## Out of scope

- Transcripts (015), content analysis (017), niche metrics (022/023).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_metrics.py` with exact expected numbers
      written in the test as literals.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --competitors --dry-run` prints the
      planned calls per tracked channel and a total.
- [ ] `.venv\Scripts\python.exe -m ytscout score --competitors` runs on the fixture DB and
      writes one `channel_metrics` row per channel × window × format.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §4.4`. Metrics are pure functions in `scoring/`; the collector and the
dashboard are the only things that touch the DB. No real API call in this session.
