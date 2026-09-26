# 045 — The competitor packet compares Shorts with Shorts

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/packets.py, src/ytscout/store/repo.py (`summary_candidates`, `summarised_videos_for_channel`), prompts/competitor_analysis.md, tests/test_competitor_analysis.py
**Milestone**: M5

## Why

DESIGN.md says Shorts and long-form are scored separately, and the packet's `meta` says
`metrics_format: shorts`, but the videos in it are whatever was summarised, in any
format. In the 018 packet (`data/packets/2026-09-25-competitors-2.json`) every one of
Curious Bone's eight videos is a 6–10 minute essay and AstroFact's first is a 612 s
long-form cut, sitting next to 90-day *Shorts* metrics. The comparison then spent a
competitor card on "6–10 minute question-titled essays" and a caveat on "their packet
medians do not reflect their Shorts", and one of the five suggestions is a long-form idea
smuggled into `caveats` because the schema has nowhere else to put it.

## Scope

- The packet carries a `format` (`shorts` for now, the value of `COMPETITOR_FORMAT`) and
  every video in it has `is_short` true. `summarised_videos_for_channel` takes the format
  and filters on `videos.is_short`; a channel with no summarised Shorts appears with an
  empty `videos` list and the prompt is told so.
- `summary_candidates` prefers Shorts for the same reason: long-form videos of tracked
  channels are summarised only after every Short candidate is done (they still get
  summarised eventually, for a later long-form packet).
- The prompt states the format up front ("every video here is a Short under 3 minutes")
  and drops the long-form escape hatch in `caveats`.
- Real `claude -p` calls: none.

## Out of scope

- A long-form competitor packet or analysis (that is a second `format` value, later).

## Acceptance criteria

- [ ] A test proves a channel with one Short and one long-form summarised video puts only
      the Short in the packet and every packet video has `is_short == True`.
- [ ] A test proves Shorts candidates are ordered before long-form candidates for the same
      channel.
- [ ] `ytscout analyse --competitors --dry-run` prints the format and per-channel Shorts
      count.
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .` clean.

## Notes

`DESIGN.md §6` (formats scored separately), `issues/closed/017-*.md` Outcome for the
packet shape. Issue 044 changes candidate *order*; this one changes *membership*. Do 044
first if both are open, the rebase is smaller that way.

## Outcome (closed 2026-09-26)

Delivered:

- `repo.summarised_videos_for_channel(conn, channel_id, limit, fmt)` now requires a format
  (`shorts` → `is_short = 1`, `long` → `is_short = 0`). Videos with unknown format
  (`is_short` NULL) are in neither, so they never reach a Shorts packet.
- `competitor_packet` passes `COMPETITOR_FORMAT`, adds a top-level `"format": "shorts"`
  (next to the existing `meta.metrics_format`) and an `is_short` field on every video. A
  channel with no summarised Shorts stays in the packet with `videos: []` and
  `views_median: null`.
- `repo.summary_plan` (and so `summary_candidates`): per channel, Shorts (outliers biggest
  first, then newest) come before long-form/unknown; across channels the whole Shorts plan
  (own first, then competitors round-robin, as 044) runs before any long-form. With
  `per_channel` the long-form tail is cut first. `SummaryCandidate` gained `is_short`
  (defaulted, so no other caller changed).
- `prompts/competitor_analysis.md` says up front that every video is a Short under 3
  minutes, drops the "mention long-form in caveats" escape hatch, and tells Claude an empty
  `videos` list means no summarised Shorts yet (note it in caveats, do not infer from
  metrics). The prompt hash changed to `3714ec9df93c`.
- `analyse --competitors --dry-run` prints `format: shorts` and `N summarised shorts` per
  channel.

Checked: tests `test_packet_holds_only_shorts` and
`test_summary_candidates_put_shorts_before_long_form` in `tests/test_competitor_analysis.py`;
`pytest -q` 554 passed; ruff format/check clean; `ytscout --help`; the dry run against the
real DB shows Curious Bone at 0 summarised Shorts (its summaries were all long-form
essays) and seven other channels with 2–15. No real `claude -p` calls.

For the next issue: Curious Bone's Shorts will be summarised on the next
`analyse --summaries` run, since Shorts now come first. A long-form packet only needs a
second `format` value passed to `summarised_videos_for_channel` and a matching metrics
filter.
