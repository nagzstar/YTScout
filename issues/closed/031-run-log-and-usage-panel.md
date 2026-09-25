# 031 — Run log, quota and Claude-usage panel in the dashboard

**Type**: AFK
**Blocked by**: 013, 017
**Add dirs**: none
**Model**: claude-opus-5-5 medium
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

## Outcome (closed 2026-09-25)

Delivered:

- **Migration 0009** adds `units_used`, `claude_calls`, `claude_input_tokens`,
  `claude_output_tokens`, `claude_cost_usd_est` and `error_tail` to `runs`. Rows from
  before it keep NULLs, which the dashboard shows as "–".
- **`cli.recorded_run`** takes the ledger total (`repo.quota_total`, the sum over *all*
  Pacific days, so a run that crosses midnight still counts) and a snapshot of
  `claude_runner.USAGE` before the block, then writes the difference when it finishes. An
  exception sets `status='error'`, keeps the last 500 characters of the traceback in
  `error_tail`, and is re-raised. It also stores `$env:YTSCOUT_RUN_LOG` in `log_path`.
  `scripts/run_weekly.ps1` now sets that variable to its `weekly-*.log`.
- **`claude_runner.USAGE`** (`Usage` dataclass, `snapshot()`, `Usage.since`): `calls`
  counts every started call, including failures. Token counts and `cost_usd_est` stay
  `None` until some call reports them, so a Claude Code version that leaves a field out
  is stored as NULL, not 0. Input tokens = `input_tokens` + `cache_creation_input_tokens`
  + `cache_read_input_tokens`. The page labels the cost as the CLI's estimate and says
  it is informational on a subscription.
- **Dashboard Runs section** (`dashboard/build.py: load_runs, run_health, units_by_day,
  pending_analyses`; `runs.html.j2`): this week's totals, the pending-analyses count, the
  latest run log path as plain text, a 14-day Pacific-day units bar chart (built from
  `quota_ledger` with zero days filled in), and the last 10 runs. Each run shows kind,
  started, duration, status, units, Claude calls and tokens, and has a collapsible
  traceback on errors. The red banner sits at the top of `<main>` and links to `#runs`.

Decisions (the issue left these open):

- **The "newest weekly run"** is every `runs` row that shares the newest `log_path`
  named `weekly-*`, one row per step. With no weekly rows at all, the newest run of any
  kind stands in. The banner shows when any of those rows is not `ok` (`quota_exhausted`
  counts) or when it started more than 8 days ago. Exactly 8 days does not trigger it.
  An empty DB also shows the banner.
- **Week** runs from Monday 00:00 UTC. The week's "not ok" count only includes finished
  rows.
- **Pending analyses** are `competitor_analyses` rows with `status='pending'` that are
  newer than the latest `ok` row. Video summaries have no pending status, so they are not
  counted.

Verified: `pytest -q` passes (506 tests, 19 of them in `tests/test_runs_panel.py`).
`ruff check` and `ruff format --check` are clean. A real `python -m ytscout score` wrote
the `score_competitors` and `score_niches` rows with `units_used=0` and
`claude_calls=0`. A real `python -m ytscout dashboard` rendered the Runs section with the
chart, and no banner because the recent runs were ok.

Not verified: nobody has looked at the panel in a browser yet. `YTSCOUT_RUN_LOG` has not
been checked through a real scheduled weekly run, because Task Scheduler is Active-only;
`test_scripts.py` still passes. No real `claude -p` call was made. The usage fields are
tested against the fake `claude`, so the cache-token keys come from the documented
`--output-format json` shape, not from a live result.
