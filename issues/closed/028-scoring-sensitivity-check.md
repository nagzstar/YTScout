# 028 — Sensitivity check: do the thresholds change the ranking?

**Type**: AFK
**Blocked by**: 027
**Add dirs**: none
**Model**: claude-fable-5-1 high
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

## Outcome (closed 2026-09-27)

Delivered `src/ytscout/scout/sensitivity.py`, the real `scout sensitivity [--niches
ID,ID] [--out PATH]` subcommand (the last scout stub is gone; `SCOUT_STUBS` is empty),
`tests/test_sensitivity.py` (14 tests) and the generated `docs/sensitivity.md`. Suite:
594 passed; ruff format and check clean; `git diff --stat config/scoring.yaml` empty.

**Run on the real DB** (3 niches: 3 deep dives and 33 aviation, both long-form; 1
dangerous animals, Shorts). Ledger before and after: 4 rows, 6,560 units total, 11
`niche_scores` rows, 53 `runs` rows, identical, so 0 units and no DB writes. The command
reads a `read_copy` of the DB, records no run and appends no score row on purpose.

What the grid says on this sample:

| id | niche | score min / default / max | opportunity min / default / max | rank stability |
|---|---|---|---|---|
| 3 | One-animal deep dives (longform) | 0.587 / 0.638 / 3.769 | 0.344 / 0.358 / 0.456 | 27/27 |
| 33 | Aviation incidents (longform) | 0.209 / 0.209 / 0.612 | 0.197 / 0.233 / 0.252 | 27/27 |
| 1 | Top 5 dangerous animals (shorts) | 0.003 / 0.003 / 0.004 | 0.088 / 0.092 / 0.134 | 27/27 |

- **The ranking never moves.** Every niche keeps its default rank in all 27 settings and
  no setting changes the top-3. With three niches and two of them a 3× score apart, this
  is expected and says little; the note in the Scope about a thin sample applies at 3 as
  much as at 2, and the report says so when the count is under 3.
- **The £ figure moves a lot.** Niche 3's score spans 0.587-3.769 (6.4×) and niche 33's
  0.209-0.612, entirely along `small_age_days`: at 180 days the small set shrinks to the
  channels that found the search within six months of being created, which are the ones
  that already had a hit, so the newcomer p50 (and the £/month built on it) jumps. That
  is survivorship in the sample, not room in the niche. `small_subs_max` moves niche 3 by
  ±25% and niche 33 not at all.
- **`outlier_multiplier` cannot change a rank.** `score` is £/month ÷ manual hours and
  `opportunity` does not enter it (§6.5), so the multiplier moves the opportunity column
  only. On this sample it moves it by under 0.06. The report states this structural fact
  up front so nobody reads the grid expecting the multiplier to matter.

**Recommendation: change no default.** The ranking is insensitive to all three
thresholds on the niches scored so far, so there is no ranking reason to move them. The
instability is in the £ estimate's anchor (issue 051), not in the thresholds: whichever
p-value or view anchor 051 picks should be checked against `small_age_days`, since that is
the axis that swings it. Re-run after 053 validates the rest of the proposals; with 10+
niches the rank-stability column will mean something.

Decisions, and why:

- **Reuse `score_niche`, patch the dict.** Each cell calls issue 024's `score_niche` with
  `{**cfg, axis: value}` against the same read-only copy, so the default cell is provably
  the `score --niches` number (the test asserts the 023 worked example under it) and the
  grid can never drift from the scorer. 27 × 3 niches is 81 scorings, well under a second.
- **Default niches = `scored` + `track`/`tracking`**, as the Scope says; `--niches` takes
  any existing id whatever its status (a validated-only niche can be run by name).
  Untagged niches are counted and skipped, as `score --niches` does.
- **Ranks are over both formats together**, matching the `score --niches` table the issue
  refers to. The report says the dashboard ranks per format. With one Shorts niche its rank
  stability is trivially 27/27.
- **"Most changes the top-3"** = the non-default setting with the most positions of the
  ordered default top-3 occupied by a different niche; `None` when no setting changes any.
- **The reading is generated**, not hand-written, so a re-run stays honest: per-axis
  "alone never changes the ranking" lines, the top-3 line, the least-stable niche and the
  widest score swing with the settings at which it is lowest and highest.
- The stub's `--max-units` is now a usage error (exit 2): the command spends no units.

Grid dimensions I would add, in order: `window_days` {60, 90, 180} (the sample's
lookback is 365 so the data is there), `outlier_floor_views` per format, the three
`weights`, and `low_confidence_min_small` / `low_confidence_cap` (every real niche so far
is either `low_confidence` or capped by it, so the cap sets the opportunity ceiling).

Unverified: nothing needed a human. The DESIGN.md §13 line says "5 hand-picked niches";
the real DB has 3 scored, so the issue's "run anyway" note wins.
