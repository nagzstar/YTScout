# 013 — `run_weekly.ps1` and `install_task.ps1`

**Type**: AFK
**Blocked by**: 007, 010
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: scripts/run_weekly.ps1, scripts/install_task.ps1, tests/test_scripts.py, README.md
**Milestone**: M1

## Why

Weekly is the heartbeat (`DESIGN.md` decision 16). This is the script that beats, and the
one that registers it. Writing them is AFK; registering the task is 014 and needs you.

## Scope

- `scripts/run_weekly.ps1 [-DryRun] [-Resume]`, Windows PowerShell 5.1-compatible syntax
  (no `??`, no ternary): from the repo root, using `.venv\Scripts\python.exe`:
  1. `collect --own --max-units 8000`
  2. `collect --competitors --max-units 8000`
  3. `collect --analytics` (skip with a logged warning if exit 4 = no token)
  4. `collect --transcripts` (015)
  5. `collect --niches --max-units 8000` (029)
  6. `analyse --summaries` then `analyse --competitors` (016/017)
  7. `score`
  8. `dashboard`
  Every step: log the command and its exit code to `logs\weekly-YYYYMMDD-HHMM.log`. Exit
  code **2** (not implemented yet) → log and continue. Exit **3** (quota exhausted) → log,
  skip the remaining collectors, still run `score` and `dashboard`, and exit 3 at the end.
  Any other non-zero → log and continue to the next step; exit 1 at the end. The dashboard
  is always rebuilt last so a partial week is still visible.
  `-DryRun` prints each command instead of running it and exits 0. `-Resume` passes
  `--resume` to the collectors (032 implements it; until then it is a no-op flag).
- `scripts/install_task.ps1 [-At "03:00"] [-Day Monday]`: `Register-ScheduledTask` named
  `YTScout Weekly`, action `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  "<repo>\scripts\run_weekly.ps1"`, trigger weekly, settings `-WakeToRun
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)`, run as the current
  user, `-Force` to replace. Print how to inspect it (`Get-ScheduledTask 'YTScout Weekly'`)
  and how to run it once by hand (`Start-ScheduledTask`). **Do not execute it** in this
  session — the guard blocks it, and it is 014's job.
- `tests/test_scripts.py`: run `powershell -NoProfile -ExecutionPolicy Bypass -File
  scripts/run_weekly.ps1 -DryRun` via `subprocess`; assert exit 0 and that the output lists
  the eight steps in order with `--max-units` on every collector. Skip the test (pytest
  `skipif`) when `powershell` is not on PATH so the suite still runs elsewhere.
- README: a "Weekly run" section that matches.

## Out of scope

- Registering or running the task (014). `--resume` behaviour (032).

## Acceptance criteria

- [ ] `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -DryRun`
      exits 0 and prints the eight commands.
- [ ] `pytest -q` passes, including `tests/test_scripts.py`.
- [ ] `install_task.ps1` parses: `powershell -NoProfile -Command "& { . { $null = [scriptblock]::Create((Get-Content scripts\install_task.ps1 -Raw)) } }"`
      exits 0 (syntax only — it is not run).
- [ ] `ruff check .` and `ruff format --check .` clean (Python only; the PS files are not
      linted).

## Notes

`DESIGN.md §9`, `§13` (PC asleep). Monday 03:00 UK is after the Pacific-midnight quota
reset at 08:00 UK? No — 03:00 UK is *before* the reset, so the run spends Sunday's Pacific
quota, which is fine as long as nothing else spent it. Put that sentence in a comment at
the top of `run_weekly.ps1` so nobody moves the time without seeing it.
