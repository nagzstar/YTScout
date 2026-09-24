# 031 — Run log, quota and Claude-usage panel in the dashboard

**Type**: AFK
**Blocked by**: 013, 017
**Add dirs**: none
**Covers**: src/ytscout/cli.py (runs context manager), src/ytscout/claude_runner.py (usage capture), src/ytscout/dashboard/templates/runs.html.j2, migration, tests/test_runs_panel.py
**Milestone**: M5

## Why

An unattended tool needs to show its own health. Failures, quota and subscription usage
belong on the page Nagz already opens, not in a log he has to remember exists.

## Scope

- `runs` rows (from 004's context manager) gain `units_used`, `claude_calls`,
  `claude_input_tokens`, `claude_output_tokens`, `claude_cost_usd_est`, `error_tail`
  (migration). The context manager reads the ledger before/after and the runner's
  accumulated usage; on an exception it records `status='error'` and the last 500 chars
  of the traceback, then re-raises.
- `claude_runner.run` accumulates `usage` and `total_cost_usd` (the CLI's estimate —
  informational on a subscription; label it so) into a module-level counter the context
  manager reads.
- Dashboard **Runs** section: last 10 runs (kind, started, duration, status, units,
  Claude calls, tokens); the current week's totals; units by day for 14 days (bar chart);
  pending analyses count; a red banner when the newest weekly run's status is not `ok` or
  when no run happened in the last 8 days.
- `logs/weekly-*.log` path of the latest run shown as text (not a link — it is local).
- Tests: seed `runs` with an ok, an error and a stale-date row → banner present /
  absent as expected; totals arithmetic; the bar chart data covers 14 days.

## Out of scope

- Alerts outside the dashboard (email, push) — not in v1.

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_runs_panel.py`.
- [ ] Running any subcommand (e.g. `score`) adds a `runs` row with `units_used` populated.
- [ ] `.venv\Scripts\python.exe -m ytscout dashboard` renders the Runs section.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §13` (subscription usage risk). Token counts are what `--output-format json`
reports; if a field is absent for a Claude Code version, store null rather than guessing.
