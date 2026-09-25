# 030 — `doctor`: every dependency checked, nothing printed that shouldn't be

**Type**: AFK
**Blocked by**: 011, 016, 033
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/doctor.py, src/ytscout/cli.py (doctor), tests/test_doctor.py
**Milestone**: M5

## Why

When the weekly run goes quiet in three months, this is the command that says why in
under ten seconds.

## Scope

- `ytscout doctor [--offline]` prints a table of checks with ✓ / ✗ / – (skipped) and a one-
  line reason, exit 1 if any **required** check fails:
  | check | required | how |
  |---|---|---|
  | Python ≥ 3.12 | yes | `sys.version_info` |
  | `config/settings.yaml` and `.env` load | yes | `settings.load()` |
  | `data_dir` writable | yes | create and delete a temp file |
  | DB opens, migrations current | yes | `connect()` and compare versions |
  | `YT_API_KEY` set | yes | non-empty; **never printed** |
  | Data API reachable | yes (offline: –) | `channels(own_id)` — 1 unit, through the ledger |
  | client secret file present | no | `Settings.client_secret_path` exists |
  | token present, refreshes, scopes right | no (offline: –) | `load_credentials()`; refresh; `check_scopes()` (monetary present, no write scopes) |
  | `claude` on PATH | yes | `shutil.which` |
  | `claude` version ≥ 2.1.259 | yes | `claude --version` |
  | `claude` logged in | yes (offline: –) | `claude -p "reply with ok" --output-format json` — one real call |
  | `pipeline_repo_path` exists | no | `is_dir()` |
  | `pipeline_coverage.yaml` present | no | exists |
  | last weekly run | no | newest `runs` row: when, status |
  | quota today | info | `remaining_today()` |
- `--offline` skips the three network checks (used by tests and by the guard-free path).
- Tests monkeypatch each probe; assert the table shape, exit codes for required vs
  optional failures, and — with a fake `.env` holding `YT_API_KEY=SECRET123` — that
  `SECRET123` appears nowhere in stdout/stderr.
- **Real calls allowed**: the two probes above (1 Data API unit, 1 `claude -p`) once each
  to confirm the non-offline path, if credentials exist.

## Out of scope

- Fixing anything it finds.

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_doctor.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout doctor --offline` exits 0 or 1 with the table and
      prints no secret material (grep the output for the key's first 6 chars — none).
- [ ] Without `--offline`, `doctor` charges exactly 1 unit (ledger before/after).
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §11 M5, §12`. Keep `doctor.py` free of imports from `collect/` so it still
runs when a collector is broken.

## Outcome (closed 2026-09-25)

Delivered `src/ytscout/doctor.py` and `ytscout doctor [--offline]`, replacing the old
presence-only doctor in `cli.py`. It prints a table (status, check, required, detail) and
`doctor: ok` or `doctor: N required check(s) failed`, and exits 1 only when a required check
fails. `doctor.py` does not import `ytscout.collect`; a test runs this in a subprocess to
prove it.

Verified by running it:
- `pytest -q`: 487 passed, including 25 in `tests/test_doctor.py`. The three old subprocess
  doctor tests in `test_cli.py` moved there, rewritten in-process with monkeypatched probes.
- `doctor --offline` on this machine: exit 0, all rows ✓ or skipped. The key's first
  6 chars are not in the output (checked by a script that loaded the key itself and
  printed only True/False). 0 units.
- `doctor` online: exit 0, every row ✓, ledger 3715 → 3716 (exactly 1 unit), one real
  `claude -p`. The token refreshed and the scopes checked out (monetary present, all
  read-only).
- `ruff check .` and `ruff format --check .` clean.

Decisions, and where this departs from the issue:
- **Secret scrubbing**: every reason passes through `Report.scrub`, which swaps the API
  key for `***`. A `googleapiclient` `HttpError` message holds the request URL with
  `key=…`, so this matters. A test injects exactly that leak, offline and online. The own
  channel id is never shown either.
- A probe that raises becomes a ✗ row naming the exception type. It never becomes a
  traceback.
- The login probe runs the issue's command plus `--allowedTools Read
  --permission-prompts none` (and `--model` if settings set one). It has no
  `--json-schema`: that is a ping, not an analysis, and the issue names the command. It
  uses `claude_runner.child_env()`, so the API-key variable is stripped.
- The token probe refreshes whenever a refresh token exists, not only when the token has
  expired, so "refreshes" is actually tested. The refresh writes the token back, as
  `auth --status` does.
- The DB row calls `connect()`, which applies any pending migrations like any other
  command, and reports `current` or `migrated now`.
- I added a **TLS CA bundle** row (optional), since the network probes depend on it. It
  also keeps the old doctor's bundle line.
- **last weekly run** shows the newest `runs` row of any kind, because no `weekly` kind
  exists (`run_weekly.ps1` runs separate commands). The detail names the kind. The row
  is ✗ unless that row's status is `ok`. It is optional.
- **ASCII fallback**: when stdout cannot encode ✓ ✗ – · (cp1252 when piped), the marks
  become `ok` / `FAIL` / `-` / `i`. Otherwise pass and fail both print as `?`.
- `probe_data_api` takes an optional `transport` so a test can prove the 1-unit charge
  with `FakeTransport`.

For the next issue: `ytscout doctor --offline` is free and safe to call from
`run_weekly.ps1` or a session. The online form costs 1 unit and one `claude -p`.
