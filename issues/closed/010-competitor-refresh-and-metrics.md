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

## Outcome (closed 2026-09-24)

Delivered: `ytscout collect --competitors [--max-units N] [--dry-run]`
(`src/ytscout/collect/competitors.py`), the pure §4.4 metrics in
`src/ytscout/scoring/metrics.py`, `ytscout score --competitors` (`src/ytscout/score.py`),
and a "Side by side" dashboard section with a 90d/365d toggle and a "views by publish
month" chart. `config/scoring.yaml` has a new `competitor_metrics:` section, which
`scoring.metrics_config` parses. The suite has 188 passing tests, 20 of them new, in
`tests/test_metrics.py` and `tests/test_collect_competitors.py`. No API units or
`claude -p` calls were spent.

How each criterion was checked, by running it:

- `pytest -q` passes. `tests/test_metrics.py` uses a 2 channel × 8 video fixture. Its
  hand-worked literals cover the median, p25, p75, max, `uploads_per_week`, `views_per_sub`,
  outlier ids, every bucket count, every title feature, velocity, the monthly sums, and
  `share_of_tracked_views`. The shares are 6600/12600 and 6000/12600, and they sum to 1.
  The collector test checks that the walk requests pages 1 and 2 of three and never
  page 3. It also checks that a known 200-day-old video keeps its single snapshot, that a
  known 30-day-old video is re-snapshotted, that rejected and candidate channels are
  skipped, and that a quota stop exits 3 with the finished batches committed.
- `collect --competitors --dry-run` on the real repo prints `channels.list x 1`, then one
  line per tracked channel (`playlistItems.list x N, videos.list x N - U unit(s)`), then
  `planned: 3 calls, 3 units for 1 tracked channel(s)`. The real DB has no approved
  channels yet, so the only tracked channel is the own channel. A test with 3 tracked
  channels checks the per-channel lines, the total of 7 units, and that the DB bytes do
  not change.
- `score --competitors` was run through the real CLI against a fixture DB, in a scratch
  repo root seeded from the test fixtures after a fake collect. It printed 12 rows for
  3 channels × 2 windows × 2 formats, and a `GROUP BY` showed exactly one row per
  combination. It exited 0. A test checks that a second run appends, making 24 rows in
  total, and that `repo.latest_channel_metrics` returns 12. I did not run it on the real
  DB.
- `ruff check .` and `ruff format --check .` are clean. `ytscout --help` runs.
- **Unverified: how the dashboard looks in a browser.** A test and a real `dashboard`
  build on the fixture DB check the structure: the own row has `class="own"` and comes
  first, the 365d panel is `hidden`, and the monthly JSON block and the table view are
  present. The page is still self-contained. I did not look at it rendered.

Decisions, and why:

- **Walk stop.** The walk stops after the first page whose oldest video, by
  `videoPublishedAt` or the DB `published_at`, is already in the DB and more than 90 days
  old. Every walked video that is new or published within 90 days is re-fetched. A
  channel's **first** refresh walks its whole playlist, which costs about 2 units per
  50 uploads. That is a one-off. I did not add a 365-day cut-off, because the issue did
  not ask for one. If a newly approved channel has thousands of uploads, the first run
  costs more.
- **Outlier baseline.** An outlier is a video in the window whose views are at least 3
  times the median of the channel's newest 30 videos **in the same format**. The
  newest 30 can reach back before the window, so a quiet 90 days does not turn every video
  into an outlier. `outlier_baseline` is stored as well.
- **Length buckets.** A duration goes into a bucket if it is at or under that bucket's
  upper bound. So a 180 s Short lands in "60–180s" (the Shorts limit), and the first
  label is "≤30s", not "<30s".
- **Percentiles** use linear interpolation between ranks, as numpy does by default.
  `uploads_per_week` is the window count ÷ (days / 7), so a channel younger than the
  window reads low.
- **Velocity: where the issue and my reading differ.** "The first snapshot within 30
  days" would always be the same snapshot as the 7-day one, so I used the **last**
  snapshot taken at or before 7 and 30 days after publish. Each figure is the median
  of those values across videos with at least 2 snapshots. The result is stored as
  `{d7, n7, d30, n30}`. It is `null` until weekly snapshots build up. There is no 24h
  figure, because weekly snapshots cannot give one.
- **`share_of_tracked_views`** is measured over the own channel plus the approved and
  watch channels, for each window × format. It is `None` when no channel has any views.
- **Format split.** A video goes to `shorts` or `longform` by its `is_short` value. A
  video with no duration has no `is_short` and is left out of both.
- **`score`** is now a real command and no longer in `STUBS`. Running `score` with no
  flag exits 1 and names `--competitors`. When 024 adds niches, it needs a new flag.
- **Chart.** The own channel is drawn first. There are at most 8 lines, using the
  dataviz skill's validated categorical palette (checked in light and dark). Beyond 8,
  the smallest channels fold into a dashed "Other (n channels)" line. Three hues are
  below 3:1 contrast on the light background, so a table view under the chart shows
  the same numbers.

For the next issues:

- **Weekly job:** run `collect --competitors --max-units N`, then `score --competitors`,
  then `dashboard`. One `collect --competitors` also refreshes the own channel's recent
  videos, but `collect --own` is still what fetches its full back catalogue.
- **Reading metrics:** `channel_metrics.window` is `'90d'` or `'365d'`, and `format` is
  `'shorts'` or `'longform'`. `metrics_json` keys are in `scoring/metrics.channel_metrics`.
  Read the latest set with `repo.latest_channel_metrics`.
