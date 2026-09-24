# 001 — Repo skeleton, package, CLI stubs, settings loader

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Covers**: pyproject.toml, src/ytscout/{__init__,__main__,cli,settings}.py, config/settings.example.yaml, .env.example, tests/test_cli.py, README.md
**Milestone**: M0

## Why

Everything after this needs a package that installs, a CLI that runs, settings that load,
and a test runner that passes. One session, then every later session starts from
`.venv\Scripts\python.exe -m ytscout --help` working.

## Scope

- `pyproject.toml`: project `ytscout`, `requires-python >= 3.12`, `src/` layout, console
  script `ytscout = "ytscout.cli:main"`. Runtime deps: `google-api-python-client`,
  `google-auth-oauthlib`, `google-auth-httplib2`, `youtube-transcript-api`, `pyyaml`,
  `jinja2`, `python-dotenv`. Dev extra `[dev]`: `pytest`, `ruff`. Pin with `>=` and the current major.
- `[tool.ruff]`: line-length 100, target py312, rules `E,F,I,UP,B`. `[tool.pytest.ini_options]`:
  `testpaths = ["tests"]`.
- `src/ytscout/cli.py`: `argparse` with subparsers for exactly these commands: `doctor`,
  `auth`, `collect`, `discover`, `packet`, `analyse`, `score`, `scout`, `dashboard`,
  `serve`, `audit`. Every one except `doctor` is a stub that prints
  `ytscout <cmd>: not implemented yet (issue NNN)` to stderr and exits **2**. Later issues
  replace stubs; the exit code 2 is what `scripts/run_weekly.ps1` will use to skip
  unimplemented steps.
- `src/ytscout/__main__.py` so `python -m ytscout` works.
- `src/ytscout/settings.py`: `load(path=None) -> Settings` (a dataclass). Reads
  `config/settings.yaml`; raises `SettingsMissing` with a message naming the example file
  if absent. Keys and defaults:
  - `own_channel_id: str` (required; falls back to `YT_CHANNEL_ID` from `.env`)
  - `data_dir: str` default `data`
  - `usd_gbp: float` default `0.78`
  - `pipeline_repo_path: str` default `C:\Users\nagaj\git\top-five-animals-1`
  - `videos_per_month: {shorts: 20, longform: 4}`
  - `quota: {daily_cap: 9000}`
  - `claude: {model: null}` (null = Claude Code's default)
  Secrets come from `.env` at the repo root (gitignored), loaded with
  `dotenv.load_dotenv(repo_root / ".env", override=False)` so a real environment variable
  wins over the file. Keys, all read through `Settings` and never printed:
  - `YT_API_KEY` — the Data API key string (`Settings.api_key: str | None`; empty until
    issue 005).
  - `YT_CLIENT_SECRET_PATH` — OAuth desktop client JSON, default
    `scripts/.secrets/client_secret.json` (`Settings.client_secret_path: Path`).
  - `YT_TOKEN_PATH` — the granted OAuth token, default `scripts/.secrets/token.json`
    (`Settings.token_path: Path`).
  - `YT_CHANNEL_ID` — fallback for `own_channel_id`.
  Relative paths resolve against the repo root (the folder holding `.env`), not the cwd.
  `Settings` exposes the two paths and never opens them.
- `config/settings.example.yaml` with every key, commented, and `.env.example` with the
  four `YT_*` keys, commented (already in the repo; keep it in step).
- `doctor` at this stage: prints Python version, whether `config/settings.yaml` loads,
  whether `YT_API_KEY` is set (yes/no), and whether the client-secret and token files
  exist (name and yes/no only, never contents). Exit 1 if settings are missing, else 0.
  Issue 030 makes it thorough.
- `tests/test_cli.py`: `--help` exits 0 and lists all 11 subcommands; each stub exits 2;
  `doctor` exits 1 with no settings file (use a tmp cwd) and 0 with a minimal one.
- `tests/test_settings.py`: defaults apply; `.env` values are picked up and a real
  environment variable wins; relative secret paths resolve against the repo root;
  `own_channel_id` falls back to `YT_CHANNEL_ID`; missing settings file raises.
- Update `README.md` "Setup" so it matches what actually works now.
- Create the venv if it does not exist: `python -m venv .venv`, then
  `.venv\Scripts\python.exe -m pip install -e ".[dev]"`.

## Out of scope

- Any YouTube or Claude call. No network.
- The SQLite store (002).

## Acceptance criteria

- [ ] `.venv\Scripts\python.exe -m pip install -e ".[dev]"` exits 0.
- [ ] `.venv\Scripts\python.exe -m ytscout --help` exits 0 and names all 11 subcommands.
- [ ] `.venv\Scripts\python.exe -m ytscout collect` exits 2 with the "not implemented" line.
- [ ] `.venv\Scripts\python.exe -m ytscout doctor` exits 1 in a directory with no
      `config/settings.yaml`, and 0 with `config/settings.example.yaml` copied in and
      `own_channel_id` filled.
- [ ] `.venv\Scripts\python.exe -m pytest -q` passes.
- [ ] `.venv\Scripts\python.exe -m ruff check .` and `ruff format --check .` are clean.
- [ ] `.gitignore` already excludes `data/`, `logs/`, `config/settings.yaml`, `.env`,
      `scripts/.secrets/`, `.venv/`; confirm nothing under those is tracked (`git status`
      clean after the run).

## Notes

`DESIGN.md §9.2` for the layout, `§12` for the setup story. Leave `config/settings.yaml`
and `.env` alone if they exist — they are the user's.
