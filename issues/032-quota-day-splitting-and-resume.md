# 032 — Quota day-splitting: stop clean at the cap, resume tomorrow

**Type**: AFK
**Blocked by**: 022, 029
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/collect/{own,competitors,niches}.py, src/ytscout/scout/validate.py, src/ytscout/store/repo.py (collector_state), scripts/run_weekly.ps1 (-Resume), tests/test_resume.py
**Milestone**: M5

## Why

A discovery-heavy week can legitimately need more than one Pacific day of quota. Today a
`QuotaExhausted` loses the run's place; after this it picks up exactly where it stopped.

## Scope

- Every collector that loops over channels or niches checkpoints to `collector_state(kind,
  key, done_at)` after each unit of work (`kind` = `own|competitors|niches|validate`,
  `key` = channel id or niche id) with a `run_id` column added so state belongs to a
  logical run, not a process.
- `--resume`: skip keys with `done_at` for the current logical run. A logical run is
  identified by the ISO week (`YYYY-Www`) for the weekly collectors and by the niche id for
  `validate`; state older than 10 days is ignored and cleared.
- On `QuotaExhausted`: commit, write the checkpoint, print `resume with: <exact command>
  after 08:00 UK`, exit **3** (already the contract from 004).
- `scripts/run_weekly.ps1 -Resume` passes `--resume` to every collector. Add to
  `install_task.ps1` a **second** trigger for the same task: Tuesday 09:00 with argument
  `-Resume` — cheap when there is nothing to resume (the collectors find no state and exit
  0 quickly), and it turns a quota stop into a one-day delay instead of a lost week. Not
  executed in this session.
- Tests: fake ledger raises after N units mid-loop → state rows exist for finished keys
  only; a second invocation with `--resume` processes only the remainder and finishes;
  without `--resume` it starts over; state from a different ISO week is ignored.

## Out of scope

- Splitting a single niche validation across days (a niche is atomic; 800 units).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_resume.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --competitors --resume --dry-run` runs and
      says what it would skip.
- [ ] `powershell ... run_weekly.ps1 -DryRun -Resume` shows `--resume` on every collector.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §13` (quota cap hit mid-run). The Tuesday re-trigger needs 014's task to be
re-registered by Nagz — say so in the Outcome so an Active issue gets written.
