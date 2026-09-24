# 028 — Sensitivity check: do the thresholds change the ranking?

**Type**: AFK
**Blocked by**: 027
**Add dirs**: none
**Covers**: src/ytscout/scout/sensitivity.py, docs/sensitivity.md, tests/test_sensitivity.py
**Milestone**: M4

## Why

`DESIGN.md §13` admits the thresholds (3×, 10k subs, 12 months, floors) are arbitrary.
This measures how much they matter on real data, so the defaults are chosen rather than
inherited.

## Scope

- `ytscout scout sensitivity [--niches a,b,c] [--out docs/sensitivity.md]`: default niches
  = all `scored`/`tracking`. For each combination in the grid
  `outlier_multiplier ∈ {2, 3, 4}` × `small_subs_max ∈ {5000, 10000, 20000}` ×
  `small_age_days ∈ {180, 365, 540}` (27 runs), recompute `opportunity` and `score` for
  every niche **in memory** (no DB writes, no API calls) using the 023 functions with a
  patched config.
- Report: for each niche, the score's min / default / max across the grid and its rank
  under each setting; a "rank stability" line per niche (how many of 27 settings keep its
  default rank); the setting that most changes the top-3. Write `docs/sensitivity.md`
  with the tables and a short reading of them. Also print the summary.
- Recommend in the Outcome whether any default should change. **Do not change
  `config/scoring.yaml`** in this issue — that is Nagz's call.
- Tests: run the grid on the 023 worked-example DB → 27 results, the default cell equals
  the 023 numbers, the report file is written and mentions the niche label.

## Out of scope

- Changing defaults. Adding grid dimensions (say what you would add).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_sensitivity.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout scout sensitivity` runs on the real DB, writes
      `docs/sensitivity.md`, spends 0 units (ledger unchanged — assert by printing before
      and after).
- [ ] `git diff --stat config/scoring.yaml` is empty at the end.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

If fewer than 3 niches are scored, run it anyway and say the sample is thin.
