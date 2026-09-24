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

## Outcome (closed 2026-09-24)

Delivered `scripts/run_weekly.ps1 [-DryRun] [-Resume]`, `scripts/install_task.ps1 [-At] [-Day]`,
`tests/test_scripts.py` and a rewritten README "Weekly run" section.

- **Nine commands, not eight.** Step 6 is two `analyse` calls. The dry-run test asserts all nine
  in order, and `--max-units 8000` on all five `collect` calls.
- **`score --competitors`, not bare `score`.** Bare `score` exits 1 ("choose what to score")
  until niche scoring lands (024). When it does, add the niche flag as another step.
- **Commands that don't exist yet** (`collect --transcripts`, `collect --niches`, `analyse ...`)
  fail argparse with exit 2. That's the same code as "not implemented", so the script logs it and
  continues. When 015/016/017/029 land, their flags just start working. Make sure those
  subcommands accept `--max-units` (the collectors) or the step keeps exiting 2.
- **`--analytics` gets `--max-units 8000` too.** The parser accepts it and ignores it for
  Analytics. It's there so every collector carries the cap.
- **`collect --resume` added to `cli.py` as a no-op flag.** Without it, `-Resume` would make every
  collector fail argparse. 032 should give it real behaviour. Tested in `tests/test_cli.py`.
- **Exit handling.** Exit 4 is a warning only on `collect --analytics`. Exit 3 skips the
  remaining *collectors* only: `analyse` still runs (it uses Claude, not YouTube quota), then
  `score` and `dashboard`, and the script exits 3. Exit 3 beats exit 1.
- **Test seams.** `-Python` and `-LogDir` params let `tests/test_scripts.py` run the real path
  against a fake `.cmd` interpreter. Three scenarios prove the 0/2/3/4/1 handling and the log
  format. The "PC asleep" comment about 03:00 UK being before the 08:00 UK reset is at the top of
  `run_weekly.ps1`.
- **install_task.ps1** registers with an Interactive logon principal for the current user
  (`WindowsIdentity.GetCurrent().Name`), so no password prompt. It will only run while Nagz is
  logged in, and `-WakeToRun` wakes a sleeping PC that has a logged-in session. If 014 wants it
  to run while logged out, switch to `-LogonType S4U`.

Verified: the `-DryRun` acceptance command exits 0 and prints the commands. The
`[scriptblock]::Create` parse check on `install_task.ps1` exits 0, and the test also parses it
with the PS AST parser. `ruff format`/`ruff check` are clean and `pytest -q` shows 213 passed.
`test_serve::test_serves_the_dashboard` timed out once under full-suite load and passed on
rerun; it's flaky and unrelated.

Unverified: `install_task.ps1` has never been executed (guard, and it's 014's job), so the
registered task's settings haven't been seen in Task Scheduler. No real weekly run was made (no
quota budget).
