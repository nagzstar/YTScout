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
