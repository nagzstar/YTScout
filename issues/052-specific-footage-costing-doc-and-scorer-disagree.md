# 052 — Make the specific-footage costing say one thing

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-sonnet-5 medium
**Covers**: docs/pipeline-audit.md, config/pipeline_coverage.yaml, src/ytscout/scoring/effort.py, DESIGN.md §6.4
**Milestone**: M5

## Why

`docs/pipeline-audit.md` says a niche that needs specific real footage is costed at the
full 2.5 h `specific_footage_hours` and "the pipeline saves nothing on it". The scorer in
`effort.py` instead keeps the pipeline's `partial` coverage and scales the 0.5 h override
by 2.5 ÷ 1.0, so the step costs 1.25 h. In 027 that is the difference between 1.85 h and
3.1 h per Short for "engineering disasters", or 37 versus 62 hours a month. Both readings
are defensible; the doc and the code must agree, and Nagz already accepted the audit doc
in 020.

## Scope

- Pick one. Default: the scorer is right (Storyblocks search-and-download scales with
  how hard the clip is to find, and the contact-sheet pass does not change), so fix the
  prose in `docs/pipeline-audit.md` and the `visuals_stock` notes in
  `config/pipeline_coverage.yaml` to describe the proportional scaling.
- If the session finds a reason the doc is right instead, change `effort.py` and its
  tests and say why in the Outcome.
- `ytscout audit` and `ytscout score` still exit 0; no API units.

## Out of scope

- Changing the 2.5 h figure itself.

## Acceptance criteria

- [ ] `docs/pipeline-audit.md`, `config/pipeline_coverage.yaml` and `effort.py` describe
      the same rule.
- [ ] `pytest -q` passes.

## Notes

`DESIGN.md §6.4` defines `specific_footage_hours`. The scorer's docstring at the top of
`effort.py` states the proportional rule.
