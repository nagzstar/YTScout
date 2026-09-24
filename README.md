# YT Scout

A local tool that scouts YouTube niches and analyses competitors for *Countdown Animal Kingdom*. Niches are ranked by **estimated £ per month ÷ manual hours per month** — money beats time put in by hand.

Full design: [`DESIGN.md`](DESIGN.md). Conventions for working in this repo with Claude Code: [`CLAUDE.md`](CLAUDE.md).

## Status

M0 in progress. Issue 001 (skeleton, CLI, settings) is closed; `issues/` lists the remaining tickets covering M0–M5.

## How it gets built

The work is in `issues/`, one vertically sliced ticket per Claude Code session. `lazyboy/` works the AFK ones unattended, in dependency order; the Active ones (credentials, approvals, judgement) are yours.

```powershell
.\lazyboy\afk.ps1 --dry-run     # what runs next, what's blocked, what needs you
.\lazyboy\afk.ps1 --limit 3     # work three AFK issues, then stop
.\lazyboy\once.ps1 005          # work an Active issue with Claude alongside
```

Before the first run: make an initial commit on `main`, install Claude Code and log in with your subscription. `issues/README.md` and `lazyboy/README.md` explain the rest.

## What it does

| Module | Question it answers |
|--------|---------------------|
| **Competitor Analyser** | Who competes with my channel, what do they do better or worse, and where are the topic gaps? |
| **Niche Scout** | Which `format × topic` niches would pay best per hour of human work, given my AI pipeline? |

Both run weekly on this PC, store 12 months of history in SQLite, and render to a static HTML dashboard.

## Prerequisites

1. **Windows 10/11**, **Python 3.12**.
2. **Google Cloud project** with **YouTube Data API v3** and **YouTube Analytics API** enabled:
   - an **API key** (Data API, public data) in `.env` as `YT_API_KEY`, and
   - an **OAuth 2.0 Desktop client** (Analytics API, your own channel's private data) saved at `YT_CLIENT_SECRET_PATH`, by default `scripts\.secrets\client_secret.json`.
   Issue 005 walks through it, including whether to reuse the production pipeline's project (shared quota) or create one for this tool. One project for this tool only. The free 10,000 units/day quota is enough; quota cannot be bought and multiple projects break the API terms.
3. **Claude Code** installed and logged in with your Claude subscription. Version ≥ 2.1.259. Check with:
   ```powershell
   claude -p "reply with the single word ok"
   ```
   No Anthropic API key is used anywhere in this project.

## Setup

What works today (issue 001): the package installs, the CLI runs, settings load, `doctor`
reports what is configured. Every other subcommand exits 2 (`not implemented yet`) until
its issue lands.

```powershell
git clone <this repo> C:\Users\nagaj\git\YTScout
cd C:\Users\nagaj\git\YTScout
python -m venv .venv                                  # Python 3.12 or newer
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

copy .env.example .env
# edit .env: YT_API_KEY, YT_CHANNEL_ID (the client secret and token paths can stay as they are)
copy config\settings.example.yaml config\settings.yaml
# edit settings.yaml: own_channel_id (or leave "" to use YT_CHANNEL_ID), usd_gbp, pipeline_repo_path

.\.venv\Scripts\python.exe -m ytscout --help
.\.venv\Scripts\python.exe -m ytscout doctor          # exit 0 when settings load; names secret files, never prints them
```

Settings resolve against the repo root (the folder holding `.env` and `pyproject.toml`), found
by walking up from the current directory, so the commands work from any subfolder. A real
environment variable beats the same key in `.env`.

Coming with later issues: `collect --own` (004), `dashboard` (007), `auth` for the Google OAuth
consent (011/012; the token is stored at `YT_TOKEN_PATH`, gitignored, never committed).

## Weekly run

```powershell
.\scripts\run_weekly.ps1 -DryRun      # print the commands, run nothing
.\scripts\run_weekly.ps1              # run the week by hand
.\scripts\run_weekly.ps1 -Resume      # pass --resume to the collectors (issue 032; no-op until then)
.\scripts\install_task.ps1            # register "YTScout Weekly" (Mon 03:00; -At "HH:mm" -Day <Day>)
Get-ScheduledTask 'YTScout Weekly'    # inspect it
Start-ScheduledTask 'YTScout Weekly'  # run it once now
```

`run_weekly.ps1` runs, in order, with `.venv\Scripts\python.exe -m ytscout`:

1. `collect --own --max-units 8000`
2. `collect --competitors --max-units 8000`
3. `collect --analytics --max-units 8000` (no OAuth token, exit 4: a logged warning)
4. `collect --transcripts --max-units 8000` (issue 015)
5. `collect --niches --max-units 8000` (issue 029)
6. `analyse --summaries`, then `analyse --competitors` (issues 016/017)
7. `score --competitors`
8. `dashboard`

Each command and its exit code go to `logs\weekly-YYYYMMDD-HHMM.log`. Exit 2 (not implemented
yet) is logged and the run continues. Exit 3 (quota exhausted) skips the remaining collectors,
still scores and rebuilds the dashboard, and the script exits 3. Any other failure is logged, the
run continues, and the script exits 1. The dashboard is always rebuilt last, so a partial week
is still visible.

The task wakes the PC (`-WakeToRun`), starts late if the PC was off (`-StartWhenAvailable`), and
is killed after 3 hours. Monday 03:00 UK is before the Pacific-midnight quota reset (08:00 UK),
so the run spends Sunday's Pacific quota; don't move it without reading the note at the top of
`run_weekly.ps1`.

## Reviewing results

Open `dashboard\index.html`. To approve or reject suggested competitors and niches, start the tiny local helper first:

```powershell
python -m ytscout serve
```

The dashboard works read-only without it.

## Repo map

```
DESIGN.md        the design document — decisions, scoring model, quota budget, data model, milestones, delivery process
CLAUDE.md        context and conventions for Claude Code
issues/          the work board — one ticket per session; closed/ and stuck/ hold the rest
lazyboy/         the unattended runner and its guard
config/          tunable YAML: scoring thresholds, RPM tiers, production steps, pipeline coverage, seeds
prompts/         one Markdown prompt per Claude call
schemas/         JSON Schema per prompt (Claude output is always schema-constrained)
src/ytscout/     the package
scripts/         PowerShell: weekly run, task install
tests/           pytest
data/ logs/      runtime state — gitignored
```

## Related

- `C:\Users\nagaj\git\top-five-animals-1` — the production pipeline for the channel. YT Scout reads it once (M3 pipeline audit) to learn which production steps are already automated. The two repos share no code.
