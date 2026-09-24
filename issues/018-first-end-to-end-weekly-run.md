# 018 — First real end-to-end weekly run and review

**Type**: Active
**Blocked by**: 012, 013, 017
**Add dirs**: none
**Covers**: scripts/run_weekly.ps1, dashboard/index.html, logs/, issues/ (new ones)
**Milestone**: M2

## Why

Everything in M1–M2 exists. This is the first time it runs as one thing and the first
time the Findings are read by the person they are for. What is wrong becomes issues.

## Scope

1. ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
   ```
   (or `Start-ScheduledTask 'YTScout Weekly'` if 014 is done).
2. Read `logs\weekly-*.log`: every step's exit code, total units, wall time.
3. Open the dashboard. Read the Findings as if a consultant wrote them:
   - Are the 5 suggestions ones you would actually make? Which not, and why?
   - Do the topic gaps hold up when you open the evidence links?
   - Is anything in the metrics table obviously wrong (a channel with 0 uploads/week that
     posts daily, a median that can't be right)?
4. Write **new issues** (033 onward) for each concrete problem, AFK where the fix is
   clear, Active where you need to decide something. Keep them small.

## Out of scope

- Fixing anything in this issue.

## Acceptance criteria

- [ ] The run completed with `score` and `dashboard` exiting 0, whatever the collectors did.
- [ ] Outcome: units spent, wall time, Claude calls made (from `runs`), one line per
      suggestion saying keep / drop / why, and the list of new issue numbers created.

## Notes

Expect the first Findings to be mediocre. The point of this issue is the list of what to
fix, not a verdict on the tool.
