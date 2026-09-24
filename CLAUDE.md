# YT Scout — context for Claude Code

Read `DESIGN.md` before doing anything non-trivial. It holds every decision, the scoring formulas, the quota budget, the data model, the milestones and the delivery process. This file is the short version plus conventions.

## What this repo is

A personal, local, Python tool for Nagz that (a) analyses competitors of his YouTube Shorts channel *Countdown Animal Kingdom* and (b) scouts new YouTube niches, ranking them by **estimated £/month ÷ manual hours/month**. One user, one Windows PC, no hosting, no auth.

## Hard constraints — never violate

- **YouTube data comes only from the official YouTube Data API v3 and YouTube Analytics API.** No scraping youtube.com, no headless browsers against YouTube, no third-party scraper APIs, no extra Google Cloud projects to multiply quota. The one grey-area dependency is `youtube-transcript-api`, isolated in `transcripts.py`.
- **Quota: 10,000 units/day, one project.** `search.list` costs 100; `channels.list`, `playlistItems.list`, `videos.list` cost 1. Every collector call goes through the quota ledger; every command that can hit the API requires `--max-units N` or `--dry-run`; the ledger stops at 9,000/day. Never call `search.list` for something a playlist or channel call can answer.
- **LLM = Claude via Nagz's subscription, invoked as `claude -p`.** No `ANTHROPIC_API_KEY`, no Anthropic SDK, no `--bare` (bare mode ignores the subscription login). All calls use `--output-format json --json-schema` with a schema from `schemas/`, `--allowedTools Read` and `--permission-prompts none`. Pass packet **file paths** in the prompt, never pipe large data on stdin.
- **Secrets never enter git and never enter a transcript.** They live in `.env` at the repo root (`YT_API_KEY`, `YT_CLIENT_SECRET_PATH`, `YT_TOKEN_PATH`, `YT_CHANNEL_ID`) and in `scripts/.secrets/` (the OAuth client JSON and token), reached only through `ytscout.settings`. `.env`, `scripts/.secrets/`, `data/`, `logs/`, `config/settings.yaml` are gitignored. Own-channel revenue and RPM stay in SQLite on this machine and are never printed.
- **Shorts and long-form are scored separately.** A niche is a `format × topic` pair.

## The work board and the runner

`issues/` is the backlog: one file per session-sized, vertically sliced issue, cut from `DESIGN.md §11`. `issues/README.md` has the conventions; `DESIGN.md §15` has the reasoning.

- **Type: AFK** — a session finishes it alone. **Type: Active** — needs Nagz (consent screens, approvals, Task Scheduler, judging a dashboard). Prefer AFK: decide in the Scope.
- **Blocked by** is the dependency graph. **Add dirs** lets a session read outside the repo.
- `.\lazyboy\afk.ps1` works AFK issues one session each, lowest number first, and verifies completion by state (file in `issues/closed/`, HEAD advanced, worktree clean). `.\lazyboy\afk.ps1 --dry-run` shows the order. `.\lazyboy\once.ps1 NNN` gives the same brief to an interactive session — that is how Active issues get done.
- `lazyboy/guard.py` is a `PreToolUse` hook that blocks quota burn, scrapers, API keys, secret reads, Active-only commands and git pushes even under `--yolo`. If it blocks you, it is right; find the other way.
- An issue's Scope says how many real API units or real `claude -p` calls it may spend. No line means none: use fixtures and the fake `claude` in `tests/fake_claude/`.
- Close an issue by appending `## Outcome (closed YYYY-MM-DD)` and `git mv` into `issues/closed/`. New work found along the way becomes a new issue, never a bigger current one.

## Layout

```
issues/       the board: NNN-*.md, closed/, stuck/, templates/
lazyboy/      run.py, guard.py, prompt.md, settings.json, afk.ps1, once.ps1
config/       scoring.yaml, rpm_tiers.yaml, production_steps.yaml, pipeline_coverage.yaml, seed_niches.yaml, settings.example.yaml
prompts/      one .md per Claude call; prompts are code, hash them
schemas/      JSON Schema per prompt
docs/         generated reports for Nagz (pipeline-audit.md, sensitivity.md)
src/ytscout/  cli.py · settings.py · youtube/ · collect/ · scout/ · transcripts.py · store/ · packets.py · claude_runner.py · scoring/ · dashboard/
scripts/      run_weekly.ps1, install_task.ps1
tests/        pytest; scoring is pure functions tested against hand-worked examples; API code against fake transports
data/, logs/  gitignored runtime state
```

CLI: `python -m ytscout <doctor|auth|collect|discover|packet|analyse|score|scout|dashboard|serve|audit>`. Exit codes: 0 ok · 1 error · 2 not implemented yet · 3 quota exhausted · 4 no OAuth token · 5 `claude` unavailable. The full table is in `DESIGN.md §9.2`.

## Conventions

- Python 3.12, `pyproject.toml`, `src/` layout, type hints everywhere, `ruff` (line length 100) for lint and format. The interpreter is `.venv\Scripts\python.exe`.
- Feedback loop before every commit: `ruff format .` → `ruff check .` → `pytest -q` → `python -m ytscout --help` → the changed subcommand with `--dry-run`.
- SQLite via stdlib `sqlite3`; schema changes are numbered files in `src/ytscout/store/migrations/`. Snapshot tables are append-only — 12-month history depends on it. Only `ytscout.store` writes to the DB.
- Scoring lives in `src/ytscout/scoring/` as pure functions that take plain dataclasses and config dicts; it never imports the DB. All thresholds come from `config/scoring.yaml`, never hard-coded.
- Swappable transports: every external client (`DataApi`, `AnalyticsApi`, transcripts, `claude_runner`) has a seam so tests run with no network. `DryRunTransport` backs every `--dry-run`.
- Every Claude analysis stored in the DB records `prompt_hash` and `schema_hash`.
- Dashboard is a single static `dashboard/index.html` built by Jinja2; Chart.js is vendored and inlined, no CDN, no external CSS/JS/fonts. The only external references allowed are `https://www.youtube.com/watch?v=` links.
- Windows is the target OS. Paths via `pathlib`; shell scripts are PowerShell 5.1-compatible.
- Fail soft on transcripts and on Claude being unavailable: store what you have, mark the rest `pending`, keep going. Fail clean on quota: commit, checkpoint, exit 3. Fail hard on ledger corruption.
- Commit messages: imperative, short, prefixed with the issue and milestone (`003 M0: quota ledger and Data API client`).

## Milestone order

M0 skeleton (001–005) → M1 competitor metrics + OAuth (006–014) → M2 competitor content analysis (015–018) → M3 pipeline audit of `C:\Users\nagaj\git\top-five-animals-1` (019–020, read-only, writes `config/pipeline_coverage.yaml`) → M4 niche scout (021–029) → M5 hardening (030–032). Details in `DESIGN.md §11`; the tickets in `issues/`.

## Related repo

`C:\Users\nagaj\git\top-five-animals-1` is the production pipeline for the channel. This repo only *reads* it (M3 audit, via `Add dirs`). If the pipeline ever consumes scout output, do it through a file contract (`data/exports/*.json`), not a code import.
