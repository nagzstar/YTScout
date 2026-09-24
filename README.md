# YT Scout

A local tool that scouts YouTube niches and analyses competitors for *Countdown Animal Kingdom*. Niches are ranked by **estimated £ per month ÷ manual hours per month** — money beats time put in by hand.

Full design: [`DESIGN.md`](DESIGN.md). Conventions for working in this repo with Claude Code: [`CLAUDE.md`](CLAUDE.md).

## Status

Design complete, backlog cut, code not started. `issues/` lists 32 tickets (25 AFK, 7 Active) covering M0–M5.

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

## Setup (once M0 lands)

```powershell
git clone <this repo> C:\Users\nagaj\git\YTScout
cd C:\Users\nagaj\git\YTScout
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

copy .env.example .env
# edit .env: YT_API_KEY, YT_CHANNEL_ID (the client secret and token paths can stay as they are)
copy config\settings.example.yaml config\settings.yaml
# edit settings.yaml: usd_gbp, pipeline_repo_path

python -m ytscout doctor          # checks API key, OAuth token, claude login, PATH
python -m ytscout collect --own   # first pull of your own channel
python -m ytscout dashboard       # builds dashboard\index.html — open it in a browser
```

`python -m ytscout auth` opens a browser window for Google OAuth consent. The token is stored at `YT_TOKEN_PATH` (`scripts\.secrets\token.json`, gitignored) and never committed.

## Weekly run

```powershell
.\scripts\install_task.ps1       # registers a Windows Task Scheduler job (Mon 03:00, wake to run)
.\scripts\run_weekly.ps1         # or run it by hand
```

`run_weekly.ps1` does: `collect → packet → analyse (claude -p) → score → dashboard`, logging to `logs\`.

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
