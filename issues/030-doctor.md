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
