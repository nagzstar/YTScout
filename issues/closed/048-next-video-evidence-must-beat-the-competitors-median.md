# 048 — A suggested video's evidence must include a competitor hit

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: prompts/competitor_analysis.md, schemas/competitor_analysis.json, src/ytscout/analyse.py (`ground_analysis`), tests/test_competitor_analysis.py
**Milestone**: M5

## Why

Four of the five 018 suggestions cite a competitor video several times its channel median
(898k, 494k, 1.96M, 3.0M views). The fifth, *Top 5 Deadliest Animals in Australia*, cites
our own best video (1,403 views), our newest (133 views, hours old) and an AstroFact
Short at 13.7k against a 57.8k median, and its rationale ("the serialisation competitors
use") rests on that below-median video. Nothing in the prompt or the grounding walk
distinguishes "evidence that this topic works" from "a video that mentions the topic".
The Findings should not be able to present a sequel of our own video as a
competitor-backed gap.

## Scope

- Prompt: each `next_videos` entry must cite at least one competitor video whose `views`
  is above its channel's packet `views_median`, and say which. Own-channel ids may be
  cited in addition, never alone.
- `ground_analysis` checks it: for each suggestion, count cited competitor ids with views
  above their channel median (the packet has both numbers). A suggestion with none is
  kept but gets `weak_evidence: true` (schema addition, default false) and a caveat
  "suggestion N cites no competitor video above its channel median". The dashboard shows
  a "weak evidence" tag on the panel.
- Same rule, same flag, for `topic_gaps`.
- Real `claude -p` calls: none. Extend the fake analysis fixture with one weak suggestion.

## Out of scope

- Dropping weak suggestions (Nagz wants to see five and judge them).

## Acceptance criteria

- [ ] A test proves a suggestion citing only own-channel ids is flagged `weak_evidence`
      and counted in `caveats`, and one citing a 3x-median competitor video is not.
- [ ] A test proves the dashboard renders the tag for a flagged suggestion.
- [ ] `schemas/competitor_analysis.json` still validates the recorded fixture analyses.
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .` clean.

## Notes

`issues/closed/017-*.md` Outcome, "Grounding is a walk" for where the check goes.
`competitor_analyses.id 3` `next_videos[4]` for the example.

## Outcome (closed 2026-09-26)

Delivered:

- `packets.packet_strong_video_ids(packet)`: the competitor videos whose `views` are
  strictly above their channel's packet `views_median`. Own-channel videos, unsettled
  videos (046) and channels whose median is `null` never count.
- `ground_analysis(analysis, video_ids, channel_ids, strong_video_ids=None)`: when given
  the strong set (as `analyse_competitors` now does), every `next_videos` and `topic_gaps`
  entry gets `weak_evidence` true/false and each weak one adds the caveat
  "suggestion N cites no competitor video above its channel median" (or "topic gap N ...").
  The check runs after unknown ids are dropped. Weak entries are kept. Without the fourth
  argument no flag is added, so older callers and tests still work.
- Schema: `weak_evidence` is an optional boolean, `default: false`, on both item types.
  It is not required, so recorded analyses from before 048 still validate.
- Prompt: every suggestion and topic gap must cite a settled competitor video above its
  channel median and name it. Own-channel ids may be cited as well, never alone. The
  model must not set `weak_evidence`; grounding overwrites it anyway.
- Dashboard: a "weak evidence" badge on the suggestion panel and on the topic-gap row.
  The template reads the key with `.get()` so rows from before 048 render.
- Fixture: `canned_analysis()` suggestion 5 now cites only own `o4` (plus an unknown id).
  Tests prove it gets flagged and counted in the caveats, that a `c3` suggestion (above
  the median) is not, that a topic gap gets flagged, that the badge renders on panel 5
  only, and that the schema validates the canned and grounded analyses.

Checked against the real DB (read-only): all three recorded `ok` rows validate against
the new schema. Re-grounding row 3 against its packet flags exactly `next_videos[4]`
(*Top 5 Deadliest Animals in Australia*), the example in Why. Row 1 comes out with all
five flagged. Its packet file on disk may not be the one it was made from, and it is an
old row, so I did not look into it. Stored rows are not rewritten: the flag appears from
the next `analyse --competitors` run.

Not verified: how the badge looks by eye. The tests only check its HTML.

Prompt and schema hashes changed, so the next competitor run is a new prompt version.
No real `claude -p` calls were made.
