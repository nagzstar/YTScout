# 014 — Register the weekly Task Scheduler job and run it once by hand

**Type**: Active
**Blocked by**: 012, 013
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: Windows Task Scheduler, logs/
**Milestone**: M1

## Why

From here on the tool runs without you. One PowerShell command and one check.

## Scope

1. In an elevated or normal PowerShell (normal is fine for a current-user task):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_task.ps1
   Get-ScheduledTask 'YTScout Weekly' | Format-List State, Triggers
   Start-ScheduledTask 'YTScout Weekly'
   ```
2. Wait for it to finish (`Get-ScheduledTaskInfo 'YTScout Weekly'` shows `LastTaskResult`).
3. Read `logs\weekly-*.log`. Open `dashboard\index.html`.

## Out of scope

- Fixing what the log reveals — write issues for it.

## Acceptance criteria

- [ ] `Get-ScheduledTask 'YTScout Weekly'` exists, `State` is `Ready`, trigger is weekly.
- [ ] The manual run produced a `logs\weekly-*.log` with every step and its exit code.
- [ ] The dashboard header shows the run and today's quota use.
- [ ] Outcome: the run's total units, its wall time, and every step that did not exit 0.

## Notes

If the machine is a laptop, check Power Options → "Allow wake timers" is on, or
`-WakeToRun` does nothing.
