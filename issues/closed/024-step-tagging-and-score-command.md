# 024 — Step tagging prompt and `score --niches`: from DB rows to `niche_scores`

**Type**: AFK
**Blocked by**: 021, 022, 023
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: prompts/niche_step_tagging.md, schemas/niche_step_tagging.json, src/ytscout/scout/tag.py, src/ytscout/scout/score.py, src/ytscout/cli.py (score), tests/test_scout_score.py
**Milestone**: M4

## Why

023 can score a `NicheSample`; this builds the sample from the DB, gets Claude to say
which production steps a niche needs, and writes the numbers the dashboard will rank.

## Scope

- `prompts/niche_step_tagging.md` + `schemas/niche_step_tagging.json`: input is up to 10
  niches (label, format, category, queries, `why_ai_able`, and 5 sample video titles from
  its validated channels) plus `production_steps.yaml`; output per niche:
  `required_steps[]` (step ids), `needs_specific_footage` (bool → use
  `specific_footage_hours` for `visuals_stock`), `notes`. The prompt reminds Claude that
  `footage_original` and `presenter` disqualify a faceless pipeline and to include them
  only when the niche truly cannot work without them.
- `ytscout scout tag [--limit N]`: for `validated` niches lacking `required_steps_json`
  for the current `prompt_hash`, batch 10 per call → write `required_steps_json`,
  `needs_specific_footage`, `tag_prompt_hash`.
- `scout/score.py`: `build_sample(conn, niche_id, cfg) -> NicheSample` from
  `niche_channels` + latest snapshots + videos in the window;
  `calibration_from_db(conn, table)` = median `rpm_usd` over own videos with ≥ 1,000 views
  in the last 90 days from `own_analytics`; `None` when absent → `calibration = 1.0` and
  flag `uncalibrated`. Long-form calibration is 1.0 with flag `longform_uncalibrated`
  until the own channel has long-form analytics.
- `ytscout score [--niches | --competitors | --all]` (extend 010's command; `--all` is the
  default and what `run_weekly.ps1` calls): for each `validated`/`scored`/`tracking`
  niche with tags → compute → append a `niche_scores` row (all the §6 intermediates, the
  p25/p50/p75, `rpm_gbp`, `est_monthly_gbp`, `manual_hours_per_month`, `score`,
  `confidence_flags_json`) and set `status='scored'` if it was `validated`. Niches without
  tags are skipped and counted.
- Print a ranked table (label, format, score, opportunity, £/mo, h/mo, flags).
- Tests: build the 023 worked example into a tmp DB (6 channels, videos with snapshots,
  one niche, tags) → `score --niches` writes a row whose numbers equal the 023 literals;
  `uncalibrated` flag when `own_analytics` is empty; untagged niche is skipped.
- **Real calls allowed**: up to **5** `claude -p` tagging runs.

## Out of scope

- Dashboard (025). Real validation data (027).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_scout_score.py` asserting the 023 numbers
      end to end through the DB.
- [ ] `.venv\Scripts\python.exe -m ytscout score` on the fixture DB prints the ranked table
      and exits 0; on an empty DB prints "nothing to score" and exits 0.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §6.3–6.5`. Keep `score` idempotent: re-running appends a new `niche_scores`
row (history), never edits one.

## Outcome (closed 2026-09-25)

Delivered in commit 2020410 (plus this close-out). Suite 433 passed (27 new in
`tests/test_scout_score.py`); ruff format and check clean.

- **`ytscout scout tag [--limit N] [--dry-run]`** (`src/ytscout/scout/tag.py`,
  `prompts/niche_step_tagging.md`, `schemas/niche_step_tagging.json`). It picks
  validated/scored/track niches whose `tag_prompt_hash` is missing or differs from the
  current prompt's hash, 10 per `claude -p` call, with each batch committed on its own. The
  packet holds label, format, category, queries, `why_ai_able`, the 5 most-viewed in-format
  titles from the niche's channels, and the production steps. The answer overwrites
  `required_steps_json` (until now the brainstorm's guess) and sets `needs_specific_footage`,
  `tag_notes`, `tag_prompt_hash`, `tag_schema_hash` and `tagged_at` (migration **0007**).
  A niche missing from Claude's answer stays untagged and is printed. A failed call exits 5,
  and batches already done stay written. The schema's step-id enum is tested against
  `production_steps.yaml`.
- **`score [--competitors | --niches | --all]`**: `--all` is the default and
  `run_weekly.ps1` now calls `score --all`. `src/ytscout/scout/score.py` has
  `build_sample`, `own_rpm_usd`, `calibration_from_db`, `score_niche`, `score_niches` and
  `format_table`. Each run appends one `niche_scores` row per tagged niche (never an edit;
  tested by scoring twice) and moves `validated` to `scored`. `track` keeps its status.
  Untagged niches are skipped and counted.
- **Worked example end to end**: the 023 example is rebuilt as DB rows (6 channels with
  two snapshots each, 36 videos with two snapshots each, one tagged niche, one own Short with
  RPM 0.10). It writes a row with every 023 number to 3 significant figures, both through
  `score_niches` and through `main(["score", "--niches"])` against a temp repo whose
  `rpm_tiers.yaml`/`pipeline_coverage.yaml` are the example's. The `uncalibrated` flag is
  set when `own_analytics` is empty. The untagged niche is skipped. With an empty DB (no file,
  and an empty file) `score` prints "nothing to score" and exits 0.

Decisions, and why:

- **Calibration** (the Scope left the window reading open): the median `rpm_usd` over own
  Shorts (the `videos.duration_s` join; a video with no row is left out) with ≥ 1,000 views. For
  each video it uses the newest `own_analytics` window (the widest when two end on the same
  day), and only if that window ends within the last 90 days. 011 collects 400-day windows, so
  this is each video's recent lifetime RPM, not a strict 90-day RPM. Calibration
  is own ÷ `animals_nature.shorts.mid`. Long-form niches use 1.0 and carry `longform_uncalibrated`.
- **Specific footage**: `manual_hours_per_video(..., needs_specific_footage=True)` costs
  `visuals_stock` at `specific_footage_hours` and multiplies a `partial` override by the
  same ratio (0.5 × 2.5 = 1.25 h). The audit measured the pipeline on generic stock.
- **Flags** stored in `confidence_flags_json`: `low_confidence`, `no_small_channels`,
  `uncalibrated`, `longform_uncalibrated`, `disqualified`. A disqualified niche stores
  `manual_hours_per_month = NULL` (not `inf`) and score 0.
- `score --competitors` alone with no DB still errors as before. `--all`/`--niches` with no
  DB print "nothing to score".
- `usd_gbp` comes from settings, or the default 0.78 when there is no `settings.yaml`.
- DESIGN.md §6.3/§6.4/§10 updated with the as-built rules and the new columns.

Real calls: **1 of 5** `claude -p` runs, on a scratch DB, not the real one. It tagged the
example niche plus two longform probes in 16 s. "Minecraft speedrun records" got
`footage_original` and was disqualified. "Rare car auction results" got
`needs_specific_footage: true`. The dangerous-animals Shorts niche got the plain 8 steps. The
schema is accepted by real Claude. The real DB has no validated niches yet (022 was
dry-run only), so no real niche was tagged or scored. Running `scout tag --dry-run` and
`score --niches` on it applied migration 0007 and printed "planned: 0 niche(s)" and
"nothing to score".

For 025/027: the dashboard should read the latest `niche_scores` row per niche and render
`confidence_flags_json` as badges. `manual_hours_per_month` can be NULL (disqualified).
027 runs `scout validate` → `scout tag` → `score --niches`.
