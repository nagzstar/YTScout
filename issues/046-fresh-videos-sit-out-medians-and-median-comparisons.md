# 046 — Videos younger than a week sit out medians and above/below-median calls

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/scoring/competitor_metrics.py (or wherever `channel_metrics` are computed), config/scoring.yaml, src/ytscout/packets.py, prompts/competitor_analysis.md, tests/test_scoring.py
**Milestone**: M5

## Why

A Short published hours before the weekly run has a view count that means nothing yet, but
it goes into the 90-day median, the outlier baseline and the packet's `views_median`, and
the prompt reads it as a below-median video. In the 018 run our newest video
(`4ZlEtbr1_rk`, 133 views, published that morning) pulled the own-channel median from
1,143 to 1,102, was cited as evidence for a suggestion, and Claude had to write the caveat
"our newest video was published hours before the packet was built" itself. For daily
posters (Beast tier, Woofy D. Luffy) the last week is 6–7 videos, so the newest-30 outlier
baseline is a fifth fresh.

## Scope

- `config/scoring.yaml` `competitor_metrics.min_age_days: 7`. A video younger than that at
  `computed_at` is excluded from `views_median`, `views_p25/p75`, `views_max`, the outlier
  baseline and the outlier count; it still counts for `uploads_per_week` and
  `video_count` reports both figures (`video_count`, `video_count_settled`).
- The packet marks each video `settled: false` when younger than `min_age_days` and
  computes `views_median` from settled videos only. The prompt tells Claude that unsettled
  videos are not evidence of performance either way.
- Hand-worked example in `tests/test_scoring.py`: 5 videos, one 2 days old with 10 views,
  median unchanged by it.

## Out of scope

- Any view-velocity model (views per day since publish); that is a later scoring idea.

## Acceptance criteria

- [ ] A test asserts the median of the worked example ignores the 2-day-old video and that
      `uploads_per_week` still counts it.
- [ ] A test asserts the packet's `views_median` excludes unsettled videos and that they
      carry `settled: false`.
- [ ] `ytscout score --competitors` against the real DB runs (exit 0) and the own channel's
      new `views_median` is 1,143 or higher (the fresh video excluded).
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .` clean.

## Notes

`DESIGN.md §6` for the metric definitions. `competitor_analyses.id 3` caveats for the
symptom.
