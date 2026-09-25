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
