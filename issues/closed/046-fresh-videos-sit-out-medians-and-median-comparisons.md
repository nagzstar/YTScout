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

## Outcome (closed 2026-09-26)

Delivered in `9bca39e`.

- `config/scoring.yaml` `competitor_metrics.min_age_days: 7`; `MetricsConfig.min_age_days`
  (dataclass default 0 so older fixtures keep their numbers; `metrics_config` requires the
  key, rejects negatives). `scoring.metrics.is_settled(published_at, now, days)` is the one
  rule; age exactly `min_age_days` is settled.
- `channel_metrics`: unsettled videos are out of `window_views`, `views_median/p25/p75/max`,
  `views_per_sub`, the outlier baseline (newest 30 *settled* in-format videos) and
  `outlier_ids`/`outlier_count`; they still count in `video_count` and `uploads_per_week`.
  New `video_count_settled`. Velocity, length buckets and title features still use every
  window video (velocity already needs ≥ 2 snapshots). Note `share_of_tracked_views` now
  divides settled views only.
- Packet: every video carries `settled`; channel `views_median` from settled videos only;
  `meta.min_age_days`. `competitor_packet(conn, *, min_age_days, now=None)` — the keyword is
  required; `analyse_competitors` and `analyse --competitors --dry-run` read it from
  scoring.yaml. The dry run prints `(N unsettled)` per channel.
- Prompt: unsettled videos are not evidence of performance either way, never in
  above/below-median lists; they may be cited for topic/hook/title. Prompt hash changed.
- DESIGN.md §4.4 gained a settling line.

Checked: `pytest -q` 558 passed; `ruff check`/`ruff format --check` clean;
`score --competitors` on the real DB exit 0, own 90d Shorts `views_median` 1,102 → 1,202
(3 of 9 videos settled — the own channel is young, so its median rests on few videos for
now); `analyse --competitors --dry-run` exit 0 (own 6 unsettled, Beast tier 2, Woofy 4).
The worked example (5 videos, one 2 days old with 10 views: median 250 not 200,
uploads_per_week 5/(90/7)) is in `tests/test_metrics.py`, where `channel_metrics` is
tested, rather than `tests/test_scoring.py`. No API units or `claude -p` calls spent.

For the next issue: dashboard median columns change meaning (settled only) with no UI
label yet; `video_count_settled` is available if a column is wanted.
