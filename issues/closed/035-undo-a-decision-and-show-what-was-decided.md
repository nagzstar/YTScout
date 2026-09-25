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

- [ ] A test posts `approved` then `undecided` for one channel and checks it is back in
      `dash.candidates` with two audit lines.
- [ ] A test renders a DB with one channel per status and asserts each row carries three
      buttons with exactly one `disabled`.
- [ ] `ytscout decide --where "status is null" --set rejected --dry-run` lists rows and
      writes nothing; without `--dry-run` it writes one `decisions` row and one JSONL
      line per channel.
- [ ] `pytest -q` passes; `ruff` clean.

## Notes

009's Outcome records the stray `watch` and duplicate `rejected` lines for Fact SL
(`UCnwMGZHnk4qVno44_eLJeBQ`); they are history and stay in the log.
