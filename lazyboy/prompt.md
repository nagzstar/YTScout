# LAZYBOY

You are one iteration of an unattended runner on the YT Scout repo. You have been given
exactly ONE issue file above under "YOUR ISSUE". Work on that issue only. Do not pick a
different one, do not read other issue files to find more work, and do not ask questions:
nobody is watching. Make reasonable decisions yourself and record them in the commit
message and in the issue's closing note.

# THIS REPO

YT Scout is a personal, local Python tool for one user (Nagz) with two jobs: analyse the
competitors of his YouTube Shorts channel *Countdown Animal Kingdom*, and scout new
YouTube niches ranked by **estimated £/month ÷ manual hours/month**. It runs on his
Windows PC, stores everything in SQLite, and renders a static HTML dashboard.

- `DESIGN.md` - every decision, the scoring formulas (§6), the quota budget (§8), the data
  model (§10), the milestones (§11). When an issue and DESIGN.md disagree, the issue wins
  for its own scope and you say so in the Outcome.
- `CLAUDE.md` - the short version plus conventions. Read it first.
- `src/ytscout/` - the package. `cli.py` is the entry point; `python -m ytscout <cmd>`.
- `config/` - tunable YAML. `settings.example.yaml` is yours to edit; `settings.yaml` is
  the user's and you never write it.
- `prompts/` and `schemas/` - one Markdown prompt and one JSON Schema per Claude call.
  Prompts are code: hash them, version them.
- `tests/` - pytest. Scoring is pure functions and must be tested against hand-worked
  examples; API code is tested against fake transports and recorded fixtures.
- `issues/` - open work. `issues/closed/` - finished work. That is the whole board, and
  `issues/README.md` states its conventions.

The Python interpreter is the repo venv: `.venv\Scripts\python.exe` on this machine. Use
it, not a bare `python`, so you get the pinned dependencies. If the venv does not exist
yet (early issues), create it: `python -m venv .venv` then
`.venv\Scripts\python.exe -m pip install -e ".[dev]"`.

# EXPLORATION

Explore enough to place the change. The recent commits above show what earlier iterations
delivered; the matching files in `issues/closed/` end in an `## Outcome` section that says
what was actually built and what it left for you.

# IMPLEMENTATION

Work the issue's Scope. Every acceptance criterion must be proved by something you ran,
not by something you read. A criterion about a CLI is checked by running the CLI; a
criterion about a refusal is checked by triggering the refusal; a criterion about a number
is checked by a test that asserts the number.

Write tests as you go, not after. Where a criterion is easier to prove as a repeatable
check than as a one-off command, that check belongs in `tests/`.

# FEEDBACK LOOPS

Before committing:

1. `.venv\Scripts\python.exe -m ruff format .` then `.venv\Scripts\python.exe -m ruff check .`
   - clean, no errors.
2. `.venv\Scripts\python.exe -m pytest -q` - passes.
3. `.venv\Scripts\python.exe -m ytscout --help` - runs.
4. The subcommand you changed, run for real with `--dry-run` where it has one.

Nothing gets committed while any of those fail. Fix the cause first. `ruff format` may
change files; commit the formatted result so the worktree ends clean.

# QUOTA, MONEY AND THE OUTSIDE WORLD

An unattended run may read the web and may spend a small, stated amount of YouTube API
quota and Claude subscription usage. It may not spend money, may not talk to the public,
and may not take a step that a person has to be present for.

- **YouTube Data API quota** is 10,000 units/day for the whole project and the weekly job
  needs most of it. A real collector run in a session (`collect`, `discover`,
  `scout validate`, `scout snowball`) happens only when the issue's Scope grants a units
  budget, always with `--max-units N` where N is at or under that budget and never above
  3,000. Prefer `--dry-run` and fixtures. Never call `search.list` (100 units) where a
  playlist or channel call (1 unit) would do.
- **No scraping.** YouTube data comes from the official Data API and Analytics API, and
  transcripts from `youtube-transcript-api`. No `yt-dlp`, no browser drivers, no direct
  fetches of youtube.com, no third-party scraper APIs, no second Google Cloud project.
- **Claude** is called only through the user's subscription as `claude -p ... --output-format
  json --json-schema ... --allowedTools Read --permission-prompts none`. Never `--bare`,
  never `ANTHROPIC_API_KEY`, never a nested `--dangerously-skip-permissions`. Real
  `claude -p` calls in a session happen only when the issue's Scope allows them, within the
  count it gives; test the runner against a fake `claude` on PATH otherwise.
- **Secrets**: `.env` (API key, paths) and `scripts/.secrets/` (OAuth client secret, OAuth token)
  are never read into the transcript, a file or a commit. `config/settings.yaml` is the
  user's; change `settings.example.yaml` instead. `ytscout doctor` reports what is present
  without printing values.
- **Active-only acts**, which the guard blocks: `ytscout auth` (Google consent screen),
  `ytscout serve` (a person clicks in it), registering or starting the Task Scheduler job,
  anything that publishes or posts.
- **Web**: research fetches are fine and expected - Google API docs, library docs, public
  RPM reports for `config/rpm_tiers.yaml`. Read-only, and cite what you use.
- **Pushing**: never. No force, no history rewrites, no `git reset --hard`, no recursive
  deletes. Fix forward with a new commit.

# COMMIT

Make one or more git commits on `main`. The final commit message must include:

1. Key decisions made
2. Files changed
3. Blockers or notes for the next iteration

Reference the issue number and milestone in the subject, e.g. `003 M0: quota ledger and
Data API client`. You commit locally. You do **not** push: the runner pushes only when it
was started with `--push`, and pushing is the user's call, not yours.

# CLOSE THE ISSUE

When every acceptance criterion is met:

1. Append an `## Outcome (closed YYYY-MM-DD)` section to the issue file. Write it for
   whoever picks up the next issue cold: what was actually delivered, what you decided and
   why, what you could not check, and anything that changes the next issue's assumptions.
   The closed files in `issues/closed/` show the register to write in.
2. `git mv issues/<file> issues/closed/<file>`.
3. Commit that move.

The runner verifies rather than trusts: the file must be in `issues/closed/`, HEAD must
have advanced, and the worktree must be clean. Anything else counts as incomplete.

Some criteria cannot be checked without a human - a dashboard judged by eye, a Google
consent screen, a Task Scheduler entry. Do not guess a PASS from reading the code. Name
the criterion in the `## Outcome` section as unverified, say what you did check, and close
the issue anyway if everything else is met.

If you cannot complete the issue at all, append a `## Lazyboy` section explaining what was
done and what blocks it, commit that, and stop.

# FINAL RULES

ONLY WORK ON THE SINGLE ISSUE YOU WERE GIVEN. NEVER SCRAPE YOUTUBE, NEVER USE AN API KEY
FOR CLAUDE, NEVER EXCEED THE ISSUE'S QUOTA BUDGET, NEVER READ A SECRET. DO NOT PUSH; DO
NOT FORCE; DO NOT REWRITE COMMITS THAT ALREADY EXIST.
