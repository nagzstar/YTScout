# 024 — Step tagging prompt and `score --niches`: from DB rows to `niche_scores`

**Type**: AFK
**Blocked by**: 021, 022, 023
**Add dirs**: none
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
