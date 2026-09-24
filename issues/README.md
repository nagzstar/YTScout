# The work board

One markdown file per session-sized piece of work, `issues/NNN-short-title.md`, sliced
vertically so each one delivers a thin end-to-end path you can demo on its own. A listing
of this directory is the whole of "what's next": open work here, finished work in
`closed/`, abandoned attempts in `stuck/`. There are no GitHub issues.

The backlog was cut from `DESIGN.md §11` (milestones M0–M5). Numbers follow dependency
order, so the runner working lowest-first builds the thing in a sensible sequence.

Start a new issue from `templates/issue.md`.

## The header the runner reads

```
**Type**: AFK
**Blocked by**: 003, 004
**Add dirs**: none
**Model**: claude-opus-5-5 medium
```

- **Type** is `AFK` or `Active`. `AFK` means a session can finish it with no further input
  from you - every decision is either already made in the Scope or safe for the session to
  make and write down. `Active` means it needs you in the room: a Google consent screen,
  a Task Scheduler prompt, a dashboard to judge by eye, a batch of competitors to approve.
  Prefer AFK: if a decision is the only thing making an issue Active, make the decision in
  the Scope and it becomes AFK.
- **Blocked by** lists the issues that must be closed first. Numbers (`003, 004`), paths
  (`issues/003-quota-ledger.md`), several lines, or `none` - all are read the same way, by
  the leading three-digit number.
- **Add dirs** lists directories outside the repo the session may read (each becomes a
  `--add-dir`). Almost always `none`; the pipeline audit is the exception.
- **Model** is the model and effort the session runs with, e.g. `claude-opus-5-5 medium`
  (the default when the line is missing) or `claude-fable-5-1 high` for issues that need
  more judgement than typing: prompt design, the scoring model, the pipeline audit, the
  sensitivity check. Both `afk.ps1` and `once.ps1` read it; `--model` / `--effort` on the
  command line override it.
- **Covers** and **Milestone** are a courtesy to whoever picks it up.

`lazyboy/` reads `Type`, `Blocked by` and `Add dirs` to decide what to run and how
(`.\lazyboy\afk.ps1 --dry-run` prints the order). Everything else in the file is for the
session doing the work.

## Budgets

Two things are finite and shared with the weekly job: **YouTube Data API units** (10,000
per day for the whole project) and **Claude subscription usage**. An issue that needs real
API calls says so in its Scope with a number (`may spend up to 300 units, always with
--max-units`); an issue that needs real `claude -p` calls says how many. No line, no spend
- the session works from fixtures and fakes. The guard enforces the mechanics; the Scope
sets the number.

## Acceptance criteria

Write them so they can be checked by running something. "`pytest -q` passes and
`tests/test_scoring.py` asserts opportunity = 0.390 for the worked example" is checkable;
"the score feels right" is a note. An unattended session proves each criterion by running
it, and says in its closing note which ones it could not.

## Closing

When every criterion passes, append an `## Outcome (closed YYYY-MM-DD)` section - what was
actually delivered, what was decided and why, what is still unverified, what the next
issue should know - and `git mv` the file into `closed/`. Do it as part of finishing the
work, not as a later tidy-up: the point of moving it is that `issues/` then lists only
open work.

An issue that cannot be finished gets a `## Lazyboy` section saying what blocks it and
moves to `stuck/`, where it waits for a human.

## Adding issues

New work found along the way (a first real run always finds some) becomes new files
numbered after the last one. Keep them vertical and session-sized; if a Scope needs more
than one session, it is two issues.
