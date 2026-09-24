# 021 — Niche model, seeds, RPM tier table, and the brainstorm prompt

**Type**: AFK
**Blocked by**: 016, 019
**Add dirs**: none
**Model**: claude-fable-5-1 high
**Covers**: config/rpm_tiers.yaml, config/seed_niches.yaml, prompts/niche_brainstorm.md, schemas/niche_brainstorm.json, src/ytscout/scout/{__init__,propose}.py, src/ytscout/cli.py (scout), tests/test_scout_propose.py
**Milestone**: M4

## Why

The scout needs candidates before it can validate any. This gives it three feeds' worth of
plumbing (seeds now, LLM now, snowball in 026) and the one table that turns views into
money.

## Scope

- **Topic categories** (the join key between niches and RPM rows), fixed list in
  `config/rpm_tiers.yaml`: `finance_business`, `tech_software`, `education_explainer`,
  `health_fitness`, `true_crime_mystery`, `history_science`, `animals_nature`,
  `entertainment_pop`, `gaming`, `lifestyle_travel`, `diy_home`, `cars_vehicles`,
  `food_cooking`, `kids_family`.
- `config/rpm_tiers.yaml`: per category × `{shorts, longform}` → `usd_rpm: {low, mid,
  high}`, `source: <url>`, `last_reviewed: 2026-09`. Research with web search: creator-
  reported and industry-survey ranges for 2025–26. Shorts RPM is small everywhere
  (typically a few cents to ~$0.15); long-form finance can be $15–30+, entertainment $1–4.
  Every row cites something; a row you cannot source gets `source: estimate` and the
  `mid` of its nearest neighbour, flagged in the Outcome.
- `config/seed_niches.yaml`: five seeds Nagz can edit, each `{format, topic, label,
  topic_category, example_search_queries: [3]}`. Include `top5-countdown ×
  dangerous-animals` (shorts) as the first so scoring has a known reference.
- `prompts/niche_brainstorm.md` + `schemas/niche_brainstorm.json`: output `niches[]` with
  `format` (shorts|longform), `topic` (slug), `label`, `topic_category` (enum above),
  `example_search_queries` (exactly 3), `why_ai_able`, `suspected_manual_steps[]`
  (production step ids from `production_steps.yaml`), `evergreen` (bool),
  `faceless_ok` (bool). The packet includes `production_steps.yaml`,
  `pipeline_coverage.yaml`, the constraints (faceless, English, evergreen preferred,
  both formats wanted), and the labels of all niches already in the DB so it explores.
- `ytscout scout propose [--count 30] [--from-seeds]`: `--from-seeds` loads the YAML →
  `niches` rows `status='proposed', source='seed'`; default runs the brainstorm →
  `status='proposed', source='llm'`. De-duplicate on `(format, topic)`; skip rows with an
  unknown `topic_category` or unknown step ids (count them in the run summary).
- Dashboard: nothing yet (025). `scout propose` prints a table of what it added.
- Tests: YAML files load and cross-validate (every category in every niche exists in the
  RPM table; every step id exists); seeds load idempotently; fake-`claude` brainstorm with
  one duplicate and one bad category → correct row count and skip counts.
- **Real calls allowed**: up to **3** `claude -p` brainstorm runs.

## Out of scope

- Validation (022), scoring (023), snowball (026).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_scout_propose.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout scout propose --from-seeds` adds 5 rows the first
      time and 0 the second.
- [ ] `config/rpm_tiers.yaml` has 14 categories × 2 formats, every row with a `source`.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §5.2, §6.3`. The RPM table is the fuzziest input in the project — `DESIGN.md
§13` says so. Do not overthink the numbers; do make every one traceable.
