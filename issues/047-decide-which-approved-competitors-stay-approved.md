# 047 — Decide which approved competitors stay approved

**Type**: Active
**Blocked by**: none
**Add dirs**: none
**Model**: claude-haiku-4-5-20251001 medium
**Covers**: data/decisions.json (via `ytscout serve`), channels.status
**Milestone**: M5

## Why

The first real comparison (018, `competitor_analyses.id 3`) spent two of its seven
competitor cards explaining that the channel is not comparable: Woofy D. Luffy is
"23–31 s photo Shorts ... mostly not about animals despite the nature hashtags" and coco
scene is "caught-on-camera clip compilations, no ranking, no hashtags". Curious Bone's
recent uploads are all 6–10 minute essays, so until 045 lands it contributes nothing to a
Shorts comparison either. Each of them costs summaries (Claude calls) and packet space
that Beast tier, CritterClipzLOL, LOWLIGHTS and AstroFact would use better, and the
`watch` status exists for exactly this: keep collecting, stop comparing.

## Scope

1. Open the served dashboard (`ytscout serve` is already running on port 8765, or start
   it) and read the three competitor cards in Findings.
2. For each of Woofy D. Luffy, coco scene and Curious Bone decide `approved`, `watch` or
   `rejected`. The session's recommendation: Woofy D. Luffy → `rejected` (not an animal
   channel), coco scene → `watch` (animal, but a clip format we will not copy; its
   "grim fate" titles are still useful as a topic signal), Curious Bone → `watch` until a
   long-form packet exists.
3. Record the decisions through the dashboard's Review controls so `decisions.json` and
   the DB agree, then run `ytscout analyse --competitors --dry-run` and confirm the packet
   lists only the channels you kept.

## Out of scope

- Finding replacements. `ytscout discover` is the monthly job; 009 covers approving.

## Acceptance criteria

- [ ] `ytscout analyse --competitors --dry-run` lists only the channels decided
      `approved`.
- [ ] `data/decisions.json` carries one entry per changed channel with the new status.

## Notes

`issues/closed/008-*.md` and `035-*.md` for the decision flow and undo. The 018 Outcome
lists what each card said.
