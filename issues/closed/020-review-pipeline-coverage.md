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

## Outcome (closed 2026-09-26)

Nagz reviewed `docs/pipeline-audit.md`, `config/pipeline_coverage.yaml` and
`config/production_steps.yaml` and accepted every coverage call and default as written.
No config changes.

`ytscout audit` exits 0. Totals: **1.20 h per Short, 1.25 h per long-form video**
(24 h/month and 5 h/month at the settings defaults).

What Claude got wrong in 019: nothing Nagz disagreed with. The three flagged guesses
(visuals 0.5 h, single-value QA across formats, research 0.2 h) stand as the working
figures; a per-format QA override remains a job for issue 023 if long-form output grows.
