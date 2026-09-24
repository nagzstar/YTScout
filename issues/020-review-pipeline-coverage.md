# 020 — Review and correct the pipeline coverage

**Type**: Active
**Blocked by**: 019
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: config/pipeline_coverage.yaml, config/production_steps.yaml
**Milestone**: M3

## Why

Claude read the pipeline; you run it. The hours in `production_steps.yaml` and the
coverage calls in `pipeline_coverage.yaml` are the denominator of every niche score, so
they should match what a week of making videos actually feels like.

## Scope

1. Read `docs/pipeline-audit.md`.
2. Open `config/pipeline_coverage.yaml`. For each step ask: is that really automated? Do I
   still touch it? Correct `coverage` and `manual_hours_override`.
3. Open `config/production_steps.yaml`. Are the default hours right *for you*? Adjust.
4. `.venv\Scripts\python.exe -m ytscout audit` — the two totals at the bottom are your
   estimated manual hours for a Short and a long-form video. Do they feel right? If not,
   keep adjusting until they do.
5. Commit with a message saying what you changed and why.

## Out of scope

- Everything else.

## Acceptance criteria

- [ ] `ytscout audit` exits 0 after your edits.
- [ ] Outcome: the two totals, and one line on anything Claude got wrong in 019 (so the
      next audit prompt can be better).

## Notes

`once.ps1 020` gives you Claude to talk it through with, but the judgement here is yours.
