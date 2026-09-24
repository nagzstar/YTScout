# 009 — Run discovery for real and approve the first competitor set

**Type**: Active
**Blocked by**: 005, 006, 008
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: data/ytscout.sqlite, data/decisions.json, config/scoring.yaml (discovery thresholds)
**Milestone**: M1

## Why

The first real 2,000 units, and the first time you look at the dashboard. What you approve
here is what 010 and 017 analyse; what you reject teaches the similarity filter.

## Scope

You, with `once.ps1 009` if you want Claude alongside.

1. Pick a day when the weekly job will not run (or before it is installed — 014).
2. ```powershell
   .venv\Scripts\python.exe -m ytscout discover --dry-run
   .venv\Scripts\python.exe -m ytscout discover --max-units 2500
   .venv\Scripts\python.exe -m ytscout dashboard
   .venv\Scripts\python.exe -m ytscout serve
   ```
   Open http://127.0.0.1:8765/ and go through the candidates.
3. Aim for **≥ 8 approved**, any number rejected, `watch` for "maybe later". Approve
   channels that actually compete for the same viewer, not just the same animals.
4. Look at the dashboard as a page: is anything unreadable, misleading, or missing? Write
   it down.

## Out of scope

- Tuning the filter in code — but if the candidates were mostly junk, say what was wrong
  with them so a follow-up issue can adjust `discovery:` thresholds.

## Acceptance criteria

- [ ] `select count(*) from channels where status='approved'` ≥ 8.
- [ ] `data/decisions.json` has one line per click.
- [ ] `quota_ledger` for the day shows the real cost of discovery (record it in the Outcome).
- [ ] The Outcome lists the approved channels (title + id), the rejection reasons in
      one line each, and every dashboard complaint — those become new issues (033+).

## Notes

If the run hits `QuotaExhausted` because something else spent the day's units, wait for
08:00 UK and run again; `discover` is idempotent.
