# 055 — Rate each niche's reused-content monetisation risk in the step tagger

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-fable-5-1 high
**Covers**: prompts/niche_step_tagging.md, schemas/niche_step_tagging.json, src/ytscout/scout/tag.py, src/ytscout/store/migrations/, src/ytscout/scoring/niches.py, src/ytscout/dashboard/, tests/, tests/fake_claude/
**Milestone**: M4

## Why

`DESIGN.md §2` decision 18. YouTube's reused-content policy is the single most common
reason faceless stock-footage-plus-TTS channels are refused Partner Programme entry, and
that is what the pipeline produces. Whether a niche survives review depends on the format:
narrated analysis, ranking with an argument, and explainers with original structure tend to
pass; clip compilations, re-cut footage and read-aloud lists tend not to. The step tagger
already reads each niche's sample titles and decides its production steps, so it is the
place to rate this. Without it a niche can rank first and never earn a penny.

## Scope

- `prompts/niche_step_tagging.md`: add a `reused_content_risk` judgement per niche with
  values `low | medium | high` and a one-sentence `reused_content_reason`. Give the model
  the policy's own framing (transformative commentary, educational or entertainment value
  added, not mass-produced or repetitive) and tell it what the pipeline does (stock clips,
  TTS voice, template captions), so it rates the *pipeline's* output in this niche, not the
  niche's best channel. Update `schemas/niche_step_tagging.json` to match; both hashes
  change, which is expected.
- `scout tag` stores the rating and reason on `niches` (new numbered migration:
  `reused_content_risk TEXT NULL`, `reused_content_reason TEXT NULL`). It must **re-tag a
  niche whose `tag_prompt_hash` differs from the current prompt's**, so the 027 niches are
  re-rated on the next real `scout tag` (issue 053 does that spend, not this issue).
- Scoring: `high` adds flag `reused_content_high` to `niche_scores.flags`; no change to
  `score` or `opportunity`. Dashboard: a risk column (low/medium/high) with the reason as
  tooltip; `high` rows shaded and shown with a warning glyph, never hidden.
- `tests/fake_claude/` learns the new schema fields; tests cover storage, the re-tag on
  hash change, the flag and the rendered column. No real `claude -p`, no API units.
- `DESIGN.md §6.4`: one paragraph describing the rating and the flag.

## Out of scope

- Turning the rating into a score multiplier or a disqualification. The user sees it and
  decides; revisit after 053.
- Rating the own channel's actual videos.

## Acceptance criteria

- [ ] `pytest -q` passes.
- [ ] `ytscout scout tag --dry-run` shows the five 027 niches queued for re-tagging.
- [ ] `ytscout dashboard` renders the column from fixture data; `ytscout score` exits 0.

## Notes

`prompts/` are code: keep the prompt's existing structure and add a section rather than
rewriting it. `DESIGN.md §7` for how `claude -p` is called; `tests/fake_claude/` for the
seam.
