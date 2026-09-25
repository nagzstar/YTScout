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

## Outcome (closed 2026-09-25)

**Registered and run.** `scripts\install_task.ps1` registered `YTScout Weekly` for
`NAGZ-PC\nagaj` (Interactive logon) with no prompt. `Get-ScheduledTask` shows `State: Ready`
and one `MSFT_TaskWeeklyTrigger`: `DaysOfWeek 2` (Monday), `WeeksInterval 1`,
`StartBoundary 2026-09-25T03:00:00+01:00`, enabled. `Start-ScheduledTask` ran it by hand;
`Get-ScheduledTaskInfo` shows `LastRunTime 25/09/2026 01:04:38`, `LastTaskResult 0`.

**The run.** Wall time 01:04:38 to 01:48:50, 44 minutes. Total YouTube units 26
(ledger 3,689 → 3,715 on Pacific day 2026-09-24: own 3, competitors 23, analytics 0). Every
step exited 0 except `collect --niches`, which exited 2 (`unrecognized arguments: --niches`,
expected until 029) and was logged as NOTE and skipped. Per step:

| step | time | result |
|---|---|---|
| collect --own | 1 s | 1 channel, 8 videos, 3 units |
| collect --competitors | 5 s | 8 channels, 342 videos, 23 units |
| collect --analytics | 1 s | 6 videos, 398 days, 10 traffic sources, 4 queries |
| collect --transcripts | 30 min 36 s | ok 32, unavailable 1, error 67 (all `IpBlocked`) |
| collect --niches | 0 s | exit 2, not implemented |
| analyse --summaries | 11 min 2 s | 40 summaries |
| analyse --competitors | 2 min 5 s | competitor_analyses id 2, ok |
| score --competitors | 0 s | ok |
| dashboard | 19 s | index.html 272 KB |

**Dashboard header** shows `Quota today 3,715 units` and
`Last run score_competitors: ok (2026-09-25T00:48:31Z)`.

**The log is partly reconstructed, and that is on me.** I armed a `tail -f` on the log to
watch the run. On Windows that handle made every `Add-Content` in `run_weekly.ps1` fail
("being used by another process"), and stopping the watcher left an orphaned `tail.exe`
holding the file until I killed it at 01:50. The script kept running and the DB is complete,
but the file lost every line after 01:04:46. Lines 20–96 were restored verbatim from the
console output (per-video transcript lines, the transcripts and niches EXIT lines) and
lines 97–105 were reconstructed from the `runs` table (summaries, competitors, score,
dashboard, DONE). Both blocks start with a `NOTE` line saying so. Issue 038 makes the
writer survive a reader and gets live progress into the log (Python buffered its output,
so nothing from a 30-minute step reached the log until it exited).

**Filed.** 038 (log survives file locks, `python -u`), 039 (67 of 100 transcript fetches
were `IpBlocked`, each costing 21 s of retries that a block never lifts; circuit breaker,
no retry on block, `blocked` status with a 7-day wait, newest videos first).

**Not checked.** Power Options "Allow wake timers": this is a desktop (`NAGZ-PC`), and the
task is Interactive, so it runs only while Nagz is logged in. The Monday 03:00 trigger has
not yet fired on its own; 018 is the first unattended week.
