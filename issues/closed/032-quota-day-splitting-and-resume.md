# 032 — Quota day-splitting: stop clean at the cap, resume tomorrow

**Type**: AFK
**Blocked by**: 022, 029
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/collect/{own,competitors,niches}.py, src/ytscout/scout/validate.py, src/ytscout/store/repo.py (collector_state), scripts/run_weekly.ps1 (-Resume), tests/test_resume.py
**Milestone**: M5

## Why

A discovery-heavy week can legitimately need more than one Pacific day of quota. Today a
`QuotaExhausted` loses the run's place; after this it picks up exactly where it stopped.

## Scope

- Every collector that loops over channels or niches checkpoints to `collector_state(kind,
  key, done_at)` after each unit of work (`kind` = `own|competitors|niches|validate`,
  `key` = channel id or niche id) with a `run_id` column added so state belongs to a
  logical run, not a process.
- `--resume`: skip keys with `done_at` for the current logical run. A logical run is
  identified by the ISO week (`YYYY-Www`) for the weekly collectors and by the niche id for
  `validate`; state older than 10 days is ignored and cleared.
- On `QuotaExhausted`: commit, write the checkpoint, print `resume with: <exact command>
  after 08:00 UK`, exit **3** (already the contract from 004).
- `scripts/run_weekly.ps1 -Resume` passes `--resume` to every collector. Add to
  `install_task.ps1` a **second** trigger for the same task: Tuesday 09:00 with argument
  `-Resume` — cheap when there is nothing to resume (the collectors find no state and exit
  0 quickly), and it turns a quota stop into a one-day delay instead of a lost week. Not
  executed in this session.
- Tests: fake ledger raises after N units mid-loop → state rows exist for finished keys
  only; a second invocation with `--resume` processes only the remainder and finishes;
  without `--resume` it starts over; state from a different ISO week is ignored.

## Out of scope

- Splitting a single niche validation across days (a niche is atomic; 800 units).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_resume.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --competitors --resume --dry-run` runs and
      says what it would skip.
- [ ] `powershell ... run_weekly.ps1 -DryRun -Resume` shows `--resume` on every collector.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §13` (quota cap hit mid-run). The Tuesday re-trigger needs 014's task to be
re-registered by Nagz — say so in the Outcome so an Active issue gets written.

## Outcome (closed 2026-09-25)

Delivered:

- **Schema**: migration `0010_collector_state_run.sql` rebuilds `collector_state` as
  `(kind, run_id, key, done_at)` with primary key `(kind, run_id, key)`. Nothing had
  written the 0001 table, so it is dropped and recreated rather than altered.
  `repo.mark_collector_done`, `collector_done_keys`, `clear_collector_state`.
- **`ytscout.collect.resume`**: `Checkpoint` (`skip` / `mark`, each mark commits),
  `iso_week` (ISO year, from the UTC clock), `open_checkpoint` (deletes rows older than
  10 days, then loads done keys only when `--resume` is given), `resume_command` /
  `resume_hint`.
- **Collectors always checkpoint**; `--resume` only decides whether done keys are skipped.
  - `own`: key = channel id, marked after its uploads walk finishes.
  - `competitors`: key = channel id, marked after its videos batches land (or when it
    has no uploads playlist). A resumed run leaves done channels out of the
    `channels.list` batch too, so a finished week costs 0 units.
  - `niches`: key = niche id. A skipped niche's channels count as already refreshed,
    so a later niche that shares them does not refresh them again.
  - `validate`: run_id `niche-<id>`, key = niche id; the niche is atomic. New
    `scout validate --resume` skips a niche validated in the last 10 days.
    (`--all-proposed` already skipped validated niches because of their status.)
- **On a quota stop**, `collect --own|--competitors|--niches` and `scout validate` print
  `resume with: .venv\Scripts\python.exe -m ytscout <the same argv> --resume after 08:00 UK`
  to stderr and exit 3, after committing. The `runs` row is `quota_exhausted` as before.
- **Dry runs** read the real DB's `collector_state` read-only and print
  `resume: would skip N channel(s) already done in 2026-W39: …` or
  `resume: nothing done yet in 2026-W39; running in full`.
- **Scripts**: `run_weekly.ps1 -Resume` already passed `--resume` to every collector;
  only its help text changed. `install_task.ps1` now registers a second trigger,
  Tuesday 09:00, configurable with `-ResumeAt` and `-ResumeDay`.

Decisions and deviations:

- **The two triggers share one `-Resume` action.** The issue asks for a Tuesday trigger
  that passes `-Resume` on the same task. Task Scheduler stores arguments on the action,
  not on the trigger, so that is not possible as written. Both triggers therefore run
  `run_weekly.ps1 -Resume`. On Monday this changes nothing, because checkpoints are keyed
  by ISO week and the Monday run is the first of its week. On Tuesday, a completed week
  costs 0 Data API units. The analyse, score and dashboard steps still re-run, but they
  spend no Data API quota. `analyse` only calls `claude -p` for items still pending.
- `--analytics` and `--transcripts` accept `--resume` and ignore it. Transcripts already
  resume on their own, because they only fetch videos not yet fetched. Analytics uses a
  separate quota.
- A channel or niche that is interrupted part-way is redone in full on resume. Its
  `channels.list` call can then add a second snapshot for that week. Snapshots are
  append-only, so the duplicate does no harm.
- The ISO week comes from the UTC date. Monday 03:00 UK is still Monday in UTC.

Verified: `pytest -q` passes (518 tests; `tests/test_resume.py` has 11). The tests cover:

- A fake ledger that stops the run after N units mid-loop leaves state rows only for
  finished keys.
- A `--resume` run processes only the remainder and finishes.
- A run without `--resume` starts over.
- State from another ISO week is ignored.
- State older than 10 days is ignored and cleared.
- `niches` and `validate` resume.
- The CLI prints the exact resume line and then resumes.

These commands also ran:

- `ruff check .` and `ruff format --check .` are clean.
- `ytscout --help` runs.
- `collect --competitors --resume --dry-run` exits 0 and says `resume: nothing done yet
  in 2026-W39`. The real DB has not been migrated to 0010 yet, so it has no state to
  skip. The "would skip" wording is asserted in a test.
- `run_weekly.ps1 -DryRun -Resume` shows `--resume` on all 5 collectors and on nothing
  else.

Not verified: the Tuesday trigger itself. `install_task.ps1` was only parsed and
text-checked, not run. **The task from 014 must be re-registered by Nagz**
(`.\scripts\install_task.ps1`) before the resume trigger exists. That needs an Active
issue.
