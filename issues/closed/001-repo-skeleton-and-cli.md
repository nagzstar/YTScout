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

## Outcome (closed 2026-09-24)

Delivered: `pyproject.toml` (src layout, console script, deps pinned to the installed
majors, ruff `E,F,I,UP,B` at line length 100, pytest `testpaths`), `src/ytscout/{__init__,
__main__,cli,settings}.py`, `config/settings.example.yaml`, `tests/test_cli.py` (12 tests),
`tests/test_settings.py` (17 tests), README "Setup" rewritten to what runs now. The venv was
created and `pip install -e ".[dev]"` exits 0. All seven acceptance criteria were proved by
running them: `--help` lists the 11 commands, every stub exits 2, `doctor` exits 1 in an
empty directory and 0 with the example copied in and `own_channel_id` filled, pytest passes
(35), ruff check and format --check are clean, `git status` shows nothing under the
ignored paths.

Decisions, and why:

- **Repo root is found by walking up from the cwd** to the first folder holding
  `pyproject.toml` or `.env`, falling back to the cwd itself. That satisfies both "relative
  paths resolve against the repo root, not the cwd" and "`doctor` exits 1 in a tmp cwd".
  `load(path=None, *, repo_root=None)` takes an explicit root so tests never touch the
  real repo's files. `Settings.repo_root` and `Settings.settings_path` are exposed for
  later issues (the store will put SQLite under `Settings.data_dir`, already a resolved
  `Path`).
- **`.env` is read with `dotenv_values`, not `load_dotenv`.** Same semantics (a real
  environment variable wins, empty values count as unset) without mutating `os.environ`,
  which would leak between tests and between commands in one process. Only the four
  `YT_*` keys are read.
- **`api_key` is `field(repr=False)`** so a stray `print(settings)` cannot leak it;
  `Settings.has_api_key` is what `doctor` and later `youtube/` code should use.
- **Stub subcommands accept any extra flags** via `parse_known_args`, so
  `collect --own --max-units 500` from the future `run_weekly.ps1` still exits 2. `doctor`
  (and every real command later) rejects unknown flags with argparse's usage error.
- **`lazyboy/` is excluded from ruff** (`extend-exclude`). `ruff format .` reformatted
  `guard.py` and `run.py`, which are the runner's own tooling and outside this issue's
  Covers; they were restored and left as committed.
- Stub messages name the issue that delivers each command (`auth` 011, `collect` 004,
  `discover` 006, `packet` 016, `analyse` 017, `score` 024, `scout` 021, `dashboard` 007,
  `serve` 008, `audit` 019).

Notes for the next issues:

- The venv on this machine is **Python 3.14.7**, not 3.12; `requires-python >= 3.12` and
  everything installed and passed on 3.14. README says "3.12 or newer".
- Installed majors pinned: google-api-python-client 2.x, google-auth-oauthlib 1.x,
  google-auth-httplib2 0.4 (pinned `>=0.2,<1`), youtube-transcript-api 1.x, pyyaml 6.x,
  jinja2 3.x, python-dotenv 1.x, pytest 9.x (`>=8,<10`), ruff 0.16 (`>=0.5,<1`).
- The user's `.env` exists at the repo root; `config/settings.yaml` does not yet, so
  `ytscout doctor` in the repo currently exits 1 with the copy-the-example message. Issue
  005 (Active) is where Nagz fills both in.
- Nothing was verified by a human; nothing in this issue needed one.
