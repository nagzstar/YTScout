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
