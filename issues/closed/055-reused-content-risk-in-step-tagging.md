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

## Outcome (closed 2026-09-27)

**Delivered**
- `prompts/niche_step_tagging.md`: a new "Reused-content risk" section after the per-niche
  bullets, structure otherwise untouched. It gives the policy's framing (transformative
  commentary, educational or entertainment value added, original structure; rejects
  mass-produced, repetitive, re-cut material), says what the pipeline makes (stock or AI
  clips, TTS voice, template captions) and tells the model to rate the *pipeline's* output
  in the niche, not the niche's best channel. `low` = the narration is the product
  (analysis, argued ranking, explainer with its own structure); `medium` = narrated lists
  or facts, common and easy to call repetitive; `high` = compilations, re-cut footage,
  read-aloud lists. `schemas/niche_step_tagging.json`: `reused_content_risk` (enum) and
  `reused_content_reason` (≤ 300 chars), both required. New hashes: prompt
  `e38a35b886a1`, schema `73c7fb5ef201` (the 027 niches carry `3dc68b52c8f6`).
- Migration `0014`: `niches.reused_content_risk TEXT` with a CHECK on the three values and
  `niches.reused_content_reason TEXT`, NULL until re-tagged. `repo.set_niche_tags` takes
  both as optional keywords (existing callers unchanged); `repo.REUSED_CONTENT_RISKS`
  is the enum. `scout tag` stores them; a value outside the enum is stored as NULL rather
  than failing the batch.
- Re-tag on prompt change needed no new code: `repo.niches_to_tag` already selects
  `tag_prompt_hash != current`. A test now proves the path end to end (old hash, NULL
  rating → queued → tagged with a rating → no longer queued).
- Scoring: `scout/score.py` adds `reused_content_high` to `niche_scores.flags` when the
  rating is `high`; `score`, `opportunity` and hours are unchanged (tested against the
  worked example). `money.FLAG_LABELS` gives it the badge text "reused-content risk:
  high", so the table and dashboard show it like the other flags.
- Dashboard: a "Reused-content risk" column after Manual h/month, reason as the cell
  tooltip, header tooltip explaining the rating. `high` rows get class `risk-high`
  (row shaded with `color-mix` on the red series colour, cell in red with a ⚠ glyph);
  `medium` in the amber series colour; a niche tagged before 055 shows "–" with the hint
  "run scout tag". Nothing is hidden or re-ordered. The sample row's colspan is 12.
- `DESIGN.md §6.4`: a paragraph on the rating and the flag; §10 lists the new columns.
- Tests (640 pass, ruff clean): storage of the rating, the re-tag on hash change, the
  fake `claude` filling the new fields from the real schema (`low` / `fake`), the flag
  and unchanged score, no flag for `low | medium | NULL`, migration CHECK and the
  `ValueError` in `set_niche_tags`, prompt and schema wording, the CLI dry-run queueing a
  niche tagged under an older prompt, and the rendered column (class, tooltip, glyph,
  unknown cell, badge). `tests/fake_claude/fake_claude.py` itself needed no change: it
  fills any schema generically; the `canned()` helper in `test_scout_score.py` now adds
  the two fields to hand-written answers because the runner validates the output.

**Decisions**
- **Three, not five, 027 niches are queued.** Real `scout tag --dry-run` plans one call
  for niches 1, 3 and 33. Niches 14 and 20 are `shelve`d, and the tagger only takes
  validated / scored / tracked niches (024's `NICHE_STATUSES_TAGGABLE`), the same rule
  under which 054 left them unscored. Widening the tagger to shelved niches would spend a
  rating on niches Nagz already said no to, so I left the rule alone. If a shelved niche
  is un-shelved it is queued automatically (old hash). The acceptance criterion is met in
  substance: every re-taggable 027 niche is queued.
- The flag lives in `scout/score.py` (which reads the niche row), not in the pure
  `scoring/` package, because the rating is a stored tag rather than a computed metric.
- Out-of-enum or missing ratings are stored as NULL and shown as unknown rather than
  failing the batch: fail soft, like transcripts.

**Checked**
- `pytest -q`: 640 passed. `ruff format` / `ruff check`: clean. `ytscout --help` runs.
- `ytscout scout tag --dry-run` (real DB, nothing written): call 1 = niches 1, 3, 33.
- `ytscout score`: exit 0, 3 real `niche_scores` rows appended, 0 API units, no flag
  raised (no niche is rated yet). `ytscout dashboard`: exit 0, the column renders with
  "–" for all five niches (pre-055 tags). No real `claude -p`, no API units spent.

**Not verified by eye**: the shading, glyph and tooltips in a browser. Checked by the
rendered-HTML test and by grepping the real `dashboard/index.html` for the header and cells.

**For whoever is next**
- 053 does the real re-tag: expect one `claude -p` call for 1, 3, 33 and then
  `score --niches` to pick up any `high`. Until then every dashboard risk cell is "–".
- The rating is display-only (issue scope). A multiplier or disqualification is a new
  issue after 053 shows what Claude actually says about the tracked niches.
- Writing files from Python on Windows gives CRLF; prompts and schemas are `eol=lf` in
  `.gitattributes` and `claude_runner.file_hash` proved insensitive to it (same hash both
  ways), but write them with `newline="\n"` anyway to keep the worktree clean.
