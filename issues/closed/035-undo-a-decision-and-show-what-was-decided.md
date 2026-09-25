# 035 — Undo a decision, and keep decided channels visible

**Type**: AFK
**Blocked by**: 009
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/dashboard/serve.py, dashboard/build.py, templates/competitors.html.j2, tests
**Milestone**: M1

## Why

In 009 a mis-click on **Watch** could only be reversed by a session editing the DB by
hand: a decided channel vanishes from the Candidates table, the Approved table has no
buttons, and rejected channels are not shown at all. Nagz also asked to bulk-clear the
19 candidates an earlier, worse filter had produced, which the session could not do
safely. Every decision needs a way back that goes through the audit log.

## Scope

- Every competitor row, whatever its status, shows its current status and the three
  buttons; the current one is disabled. Rejected channels get their own collapsed table
  so the page does not grow without bound.
- `POST /decide` already accepts any transition. Add `"undecided"` as a decision that
  sets `status` back to NULL, so a channel returns to Candidates. It is written to
  `decisions` and `decisions.json` like any other click.
- A CLI escape hatch for bulk work: `ytscout decide --channel ID --set rejected|approved|
  watch|undecided` and `--where "subs < 10000 and status is null"`, each row through
  `repo.record_decision` with an audit line, printing what it changed. No network.
- The `decisions` CHECK constraint gains `undecided`; that is migration `000N`.

## Out of scope

- Niche decisions (024/025 add those rows; give them the same buttons then).

## Acceptance criteria

- [x] A test posts `approved` then `undecided` for one channel and checks it is back in
      `dash.candidates` with two audit lines.
- [x] A test renders a DB with one channel per status and asserts each row carries three
      buttons with exactly one `disabled`.
- [x] `ytscout decide --where "status is null" --set rejected --dry-run` lists rows and
      writes nothing; without `--dry-run` it writes one `decisions` row and one JSONL
      line per channel.
- [x] `pytest -q` passes; `ruff` clean.

## Notes

009's Outcome records the stray `watch` and duplicate `rejected` lines for Fact SL
(`UCnwMGZHnk4qVno44_eLJeBQ`); they are history and stay in the log.

## Outcome (closed 2026-09-25)

Delivered in `d65fb4c`. Every criterion is proved by a test or a CLI run; the page's
look was not judged by eye.

- [x] `tests/test_serve.py::test_undecided_returns_a_channel_to_candidates` posts
      `approved` then `undecided` for one channel: it is back in `dash.candidates`, with two
      `decisions` rows and two `decisions.json` lines.
- [x] `tests/test_decide.py::test_every_row_carries_three_buttons_current_one_disabled`
      renders one channel per status (NULL, approved, rejected, watch). Each row has
      exactly three Approve/Reject/Watch buttons. On a decided row exactly one is
      disabled, the current one. A candidate has no current decision, so none of its three
      is disabled; that is the one reading of "exactly one disabled" that can apply to it.
- [x] `ytscout decide --where "status is null" --set rejected --dry-run` lists the rows
      and writes nothing: tested (DB bytes unchanged, no JSONL), and run against the real
      DB (md5 unchanged). Without `--dry-run`: one `decisions` row and one JSONL line per
      channel, tested.
- [x] `pytest -q` 529 passed; `ruff format`/`ruff check` clean; `ytscout --help` lists
      `decide`.

What was built and decided:

- **`undecided`** is a channel decision (`repo.UNDECIDED`) that sets `status` to NULL
  through `repo.record_decision`. `POST /decide` accepts it unchanged.
- **Page**: the Candidates, Approved and Rejected rows all show a Status cell and the three
  buttons. The current button is `disabled aria-pressed="true"`. Decided rows also get a
  fourth **Undo** button (`data-decision="undecided"`, "Back to Candidates"). Without it
  a decided row could only move to another decision, never back to Candidates. Rejected
  channels sit in `<details id="rejected">`, collapsed by default.
- **No migration.** The Scope asked for the `decisions` CHECK constraint to gain
  `undecided`. `0001_initial.sql` has no CHECK on `decisions.decision`, and
  `channels.status` already allows NULL, so there was nothing to widen. The value set is
  enforced in Python (`repo.DECISIONS`). The issue and the schema disagreed here, and the
  schema wins.
- **`ytscout decide`** (`--channel ID` repeatable, or `--where EXPR`; `--set
  approved|rejected|watch|undecided`; `--dry-run`):
  - `--where` is a SQL expression over `id, title, status, subs`. `subs` is the latest
    snapshot, and only `role = 'competitor'` rows are considered. The expression runs
    under a sqlite authorizer that allows reads only. A bad expression, or one with
    several statements, exits 1 and writes nothing.
  - `--dry-run` reads an in-memory copy (`read_copy`), so not even a migration touches
    the file.
  - Rows already at the target status print `unchanged` and are not re-recorded.
  - An unknown `--channel` id aborts before any write.
  - Each change is its own transaction with its audit line, and CLI lines carry
    `"via": "cli"`. `serve.record()` is now the one writer for both paths.
  - `decide` does not rebuild the dashboard. It prints a reminder to run
    `ytscout dashboard`.
- The real DB currently has **0 candidates** (`status is null`): 009's bulk clear has
  already happened. The 19 small rejected channels are all `rejected`. A dry run of
  `--channel UCnwMGZHnk4qVno44_eLJeBQ --set undecided` shows how Fact SL would go back;
  nothing was written.
- Tests changed: 008's assertions that decided rows vanish now assert that they stay
  visible, and `COMMANDS` goes from 11 to 12 entries.

Not done / next:

- Niche rows (024/025) still have only Track/Shelve with no current-state or undo. That is
  out of scope here; do it when a niche issue asks.
- The page was not viewed in a browser (`serve` is Active-only).
