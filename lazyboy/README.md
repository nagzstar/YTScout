# Lazyboy

The unattended runner. One Claude session per issue, verified, then the next. It exists
so the backlog in `issues/` can be worked through while nobody is at the machine, and so
each session starts with a small, fresh context instead of a long one.

```powershell
.\lazyboy\afk.ps1                 # every eligible AFK issue, lowest number first
.\lazyboy\afk.ps1 012             # that issue only
.\lazyboy\afk.ps1 --limit 2       # two sessions, then stop
.\lazyboy\afk.ps1 --from 020      # skip anything numbered below 020
.\lazyboy\afk.ps1 --dry-run       # what would run, what is blocked, what needs you
.\lazyboy\once.ps1 005            # the same brief, interactive - for Active issues
```

`lazyboy/afk.sh` and `lazyboy/once.sh` are the bash equivalents. Flags: `--limit N` caps
the number of sessions, `--from NNN` raises the floor, `--push` pushes after each session,
`--yolo` swaps the allowlist for `--dangerously-skip-permissions`, `--model` and `--effort`
override the per-issue `**Model**` header (default `claude-opus-5-5 medium`).

The runner uses the same Claude subscription login as an interactive `claude` session.
There is no API key anywhere in this project. It needs Claude Code ≥ 2.1.259 for
`--permission-prompts none`.

## How it picks

The lowest-numbered `issues/NNN-*.md` whose `**Type**` is `AFK` and whose every
`**Blocked by**` entry is already in `issues/closed/`. Blockers can be written as numbers
(`003, 004`), as paths, over several lines, or as `none` / `-` / `—`; matching is by the
leading three-digit number, so an issue closes its dependents whatever the file was
renamed to. `Active` issues are never picked: they need you. `--dry-run` lists them
separately so you can see what is waiting on you.

`**Add dirs**` in an issue's header lists directories outside the repo the session may
read; each becomes a `--add-dir`. The pipeline audit (issue 019) uses it to read
`top-five-animals-1` in place without copying anything.

## AFK and Active

- **AFK**: a session can finish it with no input from you. Every decision is either
  already made in the Scope or safe for the session to make and write down. The loop runs
  these one after another.
- **Active**: needs you in the room - a Google consent screen, a Task Scheduler prompt, a
  dashboard to judge by eye, a batch of competitors to approve. Work these with
  `once.ps1 NNN`, which builds the same brief and hands it to an interactive `claude`
  where you answer the prompts.

Prefer AFK. If a decision is the only thing making an issue Active, make the decision in
the Scope and it becomes AFK. The backlog is arranged so the Active issues are few and
sit at the points where real credentials or real judgement enter.

## What done means

The issue file is in `issues/closed/`, HEAD advanced, and the worktree is clean. The
runner checks all three; it does not take the session's word for it. A session that falls
short gets one resumed retry. If that fails too, the file is parked in `issues/stuck/`
with a `## Lazyboy` note and the loop moves on. Stuck issues wait for you.

## What it may not do

`lazyboy/prompt.md` is the standing brief. `lazyboy/settings.json` is the allowlist and
`lazyboy/guard.py` runs as a `PreToolUse` hook on `Bash` **and** on `Edit`/`Write`/
`MultiEdit`. The Bash rules read the whole command string, so a compound command cannot
smuggle a denied call past the prefix-matched allowlist. **Hooks still run under
`--dangerously-skip-permissions`**, which is what makes `--yolo` safe here.

The guard blocks:

- **Quota burn** - `ytscout collect`, `discover`, `scout validate`, `scout snowball`
  without `--max-units` or `--dry-run`, and any `--max-units` above 3,000. The weekly job
  needs most of the 10,000/day; a session gets a slice the issue names.
- **Scraping** - installing `yt-dlp`, `pytube`, Selenium, Playwright and friends; fetching
  `youtube.com` with curl/wget/requests; `gcloud projects create`. The official APIs are
  the only source (DESIGN.md decision 3), because a ToS strike lands on a monetised channel.
- **Claude outside the subscription** - any mention of `ANTHROPIC_API_KEY`, `claude
  --bare`, a nested `--dangerously-skip-permissions`.
- **Secrets** - reading `.env`, `scripts/.secrets/*`, `client_secret*.json`,
  `token.json` into the transcript, or copying/sending them anywhere; writing
  `config/settings.yaml`, the SQLite file, or any file containing an API key.
- **Active-only acts** - `ytscout auth`, `ytscout serve`, `schtasks` /
  `Register-ScheduledTask` / `install_task.ps1`.
- **Git and the shell** - pushes, force pushes, rebases, `reset --hard`, `filter-branch`,
  `gh` writes, `rm -rf`, `Remove-Item -Recurse`.

### What it spends

Reading is free. A session may spend YouTube Data API units only when its issue grants a
budget, and Claude subscription usage only when its issue allows real `claude -p` calls
(the runner itself is a subscription session too). It never spends money: there is no paid
API in this project.

## Pushing

Off by default. The runner commits to local `main` and leaves it there, so you can read
the diff before anything leaves the machine. `--push` pushes after each issue, and then
the run refuses to start unless `main` already matches `origin/main`.

## Output

`lazyboy/logs/NNN.jsonl` is the full stream for each issue. `lazyboy/metrics.csv` gets a
row per attempt with turns, duration and the cost the CLI estimates (informational on a
subscription). Both are gitignored - they are a record of a run on this machine, not part
of the project.
