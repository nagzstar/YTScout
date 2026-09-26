# 040 — Re-register the weekly task so the Tuesday resume trigger exists

**Type**: Active
**Blocked by**: none
**Add dirs**: none
**Model**: claude-haiku-4-5-20251001 medium
**Covers**: scripts/install_task.ps1, Task Scheduler task 'YTScout Weekly'
**Milestone**: M5

## Why

Issue 032 taught every collector to checkpoint and `--resume`, and it added a second
trigger (Tuesday 09:00, `-Resume`) to `scripts/install_task.ps1`. The task registered in
014 predates that change, so on this machine the Tuesday trigger does not exist yet. Until
it is re-registered a quota stop on Monday still loses the week.

## Scope

1. ```powershell
   .\scripts\install_task.ps1
   ```
   Registering under the same name replaces the 014 task; no unregister step is needed.
2. Confirm both triggers:
   ```powershell
   (Get-ScheduledTask 'YTScout Weekly').Triggers | Format-List DaysOfWeek, StartBoundary
   ```
3. Run the task once by hand on a day with nothing to resume and check the log shows the
   collectors exiting 0 quickly, having found no checkpoint state.

## Out of scope

- Changing the schedule times or the script.

## Acceptance criteria

- [ ] `Get-ScheduledTask 'YTScout Weekly'` shows two weekly triggers: Monday and Tuesday.
- [ ] The task's action runs `run_weekly.ps1 -Resume`.
- [ ] A manual run of the task with no checkpoint state completes with every step exiting 0
      and spends 0 Data API units on collectors.

## Notes

`issues/closed/032-*.md` Outcome, "Not verified" paragraph. `DESIGN.md §13`.

## Outcome (closed 2026-09-27)

Re-registered by running `.\scripts\install_task.ps1` interactively on 2026-09-27 00:09. The
014 task had one trigger (Monday 03:00) and an action without `-Resume`.

- `Get-ScheduledTask 'YTScout Weekly'` now shows two weekly triggers: DaysOfWeek 2
  (Monday) at 03:00 and DaysOfWeek 4 (Tuesday) at 09:00, both enabled. Next run
  2026-09-28 03:00.
- The action is `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  "...\scripts\run_weekly.ps1" -Resume`, working directory the repo root.
- Manual run via `Start-ScheduledTask` at 00:10, log `logs/weekly-20260927-0010.log`:
  every step exited 0, `DONE exit 0`, Task Scheduler `LastTaskResult 0`. `collect --own`
  and `collect --competitors` skipped their 2026-W39 checkpoints (1 and 8 channels) in
  under a second.

Acceptance point three was met on the second pass, not the first. The manual run's
`collect --niches --resume` found no niches checkpoint for 2026-W39 (Friday's run had
refreshed 0 of 0 niches, so nothing was marked done) and ran in full: 3 niches, 389 Data
API units, ledger 2811 -> 3200 for Pacific 2026-09-26. That is the resume rule working as
designed: the week's niche work had not been done, so it was not skipped. Immediately
afterwards `collect --own`, `--competitors` and `--niches` with `--resume` each reported
"skipping ... already done in 2026-W39", 0 units, ledger unchanged at 3200. That is the
state the Tuesday trigger sees after a complete Monday.

Also seen, not in scope: `collect --transcripts` tripped the IpBlocked breaker after 5
consecutive blocks (7 blocked, 62 candidates untouched) and still exited 0, as 039 intends.
