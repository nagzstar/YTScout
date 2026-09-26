# 053 — Second scout run: validate every open proposal

**Type**: Active
**Blocked by**: 049, 052, 054, 055
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: data/ytscout.sqlite, dashboard/index.html
**Milestone**: M4

## Why

Niche Scout is now the primary goal: pick a format × topic for a long-term, AI-assisted,
monetised channel. 027 validated 5 of 35 proposals and found its samples polluted. Once
049 (relevance filter) and 052 (footage costing) land, the other 30 proposals need real
data before any ranking means anything. The weekly job (029) only refreshes niches that
are already tracked; nothing validates proposals unattended.

## Scope

- May spend **up to 18,000 units in total, at most 5,500 per day**, always with
  `--max-units`. Never on a Monday (the weekly job runs Monday 03:00 with 8,000 reserved).
  About 560 units per niche after the early stop, so three days of quota.
- Each day, on the days you choose:
  ```powershell
  .venv\Scripts\python.exe -m ytscout scout validate --all-proposed --resume --max-units 5500
  ```
  Exit 3 means the day's budget is spent; run again tomorrow. `--resume` skips niches
  validated in the last 10 days.
- When every proposal is validated (or shelved by hand in the dashboard first if a label
  is obviously not worth 560 units — shelve, do not delete):
  ```powershell
  .venv\Scripts\python.exe -m ytscout scout tag --limit 40
  .venv\Scripts\python.exe -m ytscout score
  .venv\Scripts\python.exe -m ytscout dashboard
  ```
  `scout tag` is real `claude -p`: up to 8 calls (5 niches per call).
- Read the Niches table with Claude alongside, sorted by months to Partner Programme
  (054) with the reused-content risk column (055) visible. Track the 10 niches that reach
  monetisation soonest regardless of format, skipping `high` reused-content risk unless
  the reason is unconvincing, and shelve the rest, so the weekly refresh starts building
  12-month history for the candidates only.
- Do not run `sensitivity` (028) or change `config/scoring.yaml` here.

## Out of scope

- Fixing what the run finds — issues.
- Re-validating the five 027 niches (their samples are stored; 049 re-scores them).
- The £ anchor (051) and calibration (050). They change the money column, not the
  samples, so the run is still worth doing before they are decided.

## Acceptance criteria

- [ ] Every niche in the `niches` table has status `track` or `shelve`; none is left
      `proposed` or `validated`.
- [ ] `ytscout score` exits 0 and every tracked niche has a `niche_scores` row.
- [ ] Outcome: units spent per day and in total, the number of `claude -p` calls, the
      ranked table (label / months to YPP / reused-content risk / score / opportunity / £ band /
      h per month / small-channel count) for the ten tracked niches, and the new issue numbers for anything wrong.
      No own-channel revenue or RPM figures.

## Notes

`once.ps1 053` for Claude alongside. Read the 027 Outcome first: the samples it saw and
why it distrusted them. `DESIGN.md §5.3` describes validation, §6 the scoring.
