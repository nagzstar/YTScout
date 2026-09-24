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

- [x] `pytest -q` passes, including `tests/test_scout_propose.py`.
- [x] `.venv\Scripts\python.exe -m ytscout scout propose --from-seeds` adds 5 rows the first
      time and 0 the second.
- [x] `config/rpm_tiers.yaml` has 14 categories × 2 formats, every row with a `source`.
- [x] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §5.2, §6.3`. The RPM table is the fuzziest input in the project — `DESIGN.md
§13` says so. Do not overthink the numbers; do make every one traceable.

## Outcome (closed 2026-09-24)

Delivered in commits 8861873 and db5200f (plus this close-out).

- `config/rpm_tiers.yaml`: 14 categories × `{shorts, longform}`, every row with `usd_rpm`
  `{low, mid, high}`, `source` and `last_reviewed: 2026-09`. **Flag**: 12 of the 14 shorts
  rows are `source: estimate` (all but `finance_business` and `true_crime_mystery`). Shorts
  RPM is rarely reported per category, so those rows take the mid of their nearest
  sourced neighbour. Revisit when 028 (sensitivity) says the ranking depends on them.
- `config/seed_niches.yaml`: 5 seeds. `scout propose --from-seeds` added 5 rows on the first
  real run and 0 on the second (5 duplicates skipped), as required.
- `prompts/niche_brainstorm.md` + `schemas/niche_brainstorm.json`, `src/ytscout/scout/propose.py`,
  `scout propose [--count N] [--from-seeds] [--dry-run]`, migration 0005 (`niches.meta_json`
  carrying why_ai_able, evergreen, faceless_ok, required steps, and prompt/schema hashes
  plus packet path for LLM rows).
- **Real calls spent: 1 of 3** `claude -p` brainstorm runs. 30 candidates asked, 30 added,
  0 duplicates, 0 unknown categories, 0 unknown step ids, 121 s, packet
  `data/packets/2026-09-24-niche_brainstorm-1.json`, prompt hash `c72182060a35`, schema
  hash `d35b4b4911f8`. The DB now holds 35 proposed niches (5 seed, 30 llm).
- Tests: `tests/test_scout_propose.py`; suite 329 passed; `ruff check` and `ruff format
  --check` clean. `tests/test_store.py` migration list bumped to five.

Found along the way, fixed outside this issue's scope in commit 8861873: the fake-`claude`
fixture built a new byte-unique `claude.exe` per test, and Avast CyberCapture held each one
for cloud analysis. That is what killed the first AFK session on this issue mid-run. The
fixture now builds one deterministic exe into `tests/fake_claude/build/` (gitignored).
