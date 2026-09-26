# 044 — Summarise each channel's top videos, not the newest forty overall

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/store/repo.py (`summary_candidates`, `summarised_videos_for_channel`), src/ytscout/analyse.py, src/ytscout/packets.py, config/scoring.yaml, tests
**Milestone**: M5

## Why

`summary_candidates` takes the 40 newest videos across every tracked channel, and the
packet takes each channel's newest summarised videos. Two things go wrong in the real DB:

1. Channels that post daily (Beast tier ~7/week, Woofy D. Luffy ~6/week) crowd out the
   rest. After two runs coco scene and LOWLIGHTS have 0 summaries and AstroFact 1, so the
   comparison said "LOWLIGHTS and coco scene have no videos in the packet" and judged
   AstroFact from a single statues video, calling it "not an animal channel" when its two
   biggest Shorts of the quarter are *Top 5 weirdest Animal Eggs* (2.5M views) and *Top 5
   Largest Sea Creatures* (1.8M).
2. Newest is the wrong axis anyway. The findings Nagz wants ("what works for them that we
   don't do") come from a channel's outliers, not from whatever it posted on Tuesday.
   Beast tier's 5.9M *Animals With Diets You Won't Believe* and CritterClipzLOL's 17M
   *Sea Creatures That Keep Growing* were not in the packet.

## Scope

- Candidate order: per channel, first the outliers (views >= `outlier_multiplier` x the
  channel's median; reuse the `competitor_metrics` values in `config/scoring.yaml`), then
  the newest; interleave channels round-robin so a 40-video run gives every tracked
  channel a share. Own-channel videos always first (there are few).
- Add `--per-channel N` (default from `config/scoring.yaml`, e.g. 8) so the cap is
  explicit. Keep `--limit` as the run total.
- `summarised_videos_for_channel` (the packet side) orders by views desc, then
  published_at desc, so the packet shows a channel's best work; the prompt already reads
  `views` per video.
- The "no transcript yet" rule stays: a titles-only summary is redone once an `ok`
  transcript exists.
- Real `claude -p` calls: none. Use the fake `claude`.

## Out of scope

- Changing the summary prompt or schema.

## Acceptance criteria

- [ ] A test with three channels (one posting daily) proves a 12-video run summarises at
      least 3 videos of each channel and that each channel's biggest video is among them.
- [ ] A test proves the packet's videos for a channel are its highest-view summarised
      videos.
- [ ] `ytscout analyse --summaries --dry-run` prints the per-channel split.
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .` clean.

## Notes

`competitor_analyses.id 2` and `data/packets/2026-09-25-competitors-1.json` show the
skew. `DESIGN.md §6` for the outlier rule.

## Outcome (closed 2026-09-26)

Delivered:

- `repo.summary_plan(conn, prompt_hash, limit, *, per_channel, outlier_multiplier,
  outlier_window)` returns `SummaryCandidate`s (video, channel, views, `outlier`) in run
  order; `summary_candidates` is now its id list. Order: own channel's queue first, then
  competitors round-robin by channel id. Each queue is the channel's eligible outliers
  (latest views >= `competitor_metrics.outlier_multiplier` x the median of the channel's
  newest `outlier_window_videos` in the same format, summarised or not; biggest first),
  then its other eligible videos newest first, cut at `per_channel`. The eligibility rule
  is unchanged, including redoing titles-only summaries once an `ok` transcript exists.
- `config/scoring.yaml` gains `video_summaries.per_channel: 8`
  (`scoring.summaries_per_channel`). `analyse --per-channel N` overrides it; `--limit`
  stays the run total. `analyse --summaries` now reads `config/scoring.yaml` and exits 1
  if it is missing or malformed.
- `summarised_videos_for_channel` (the packet side) orders by views desc (NULL last),
  then published_at desc.
- `analyse --summaries --dry-run` prints each candidate as `id channel: views (outlier|
  newest)` and a `per channel` block with counts and outlier counts. Run against the real
  DB it showed 7 channels sharing 40 slots (Beast tier 7 with 5 outliers, AstroFact 6 with
  1, LOWLIGHTS 1 — only one eligible video left there).

Tests: `tests/test_summary_plan.py` — three channels (one posting daily, 60 videos) and a
12-video run give each channel >= 3 videos led by its biggest; per_channel cap and
own-first; titles-only redo; packet videos are the highest-view summaries; config
validation. Existing tests in `test_packets.py` updated for own-first order, and both CLI
fixtures copy `config/scoring.yaml`.

Not checked: how the next real comparison reads with the new packet (no real `claude -p`
calls were allowed). Channels with few eligible videos (LOWLIGHTS) are limited by
transcript attempts, not by this ordering — `collect --transcripts` decides which videos
are eligible and still goes newest first.
