# 019 — Pipeline audit: production steps and what `top-five-animals-1` already automates

**Type**: AFK
**Blocked by**: 001
**Add dirs**: C:\Users\nagaj\git\top-five-animals-1
**Model**: claude-fable-5-1 high
**Covers**: config/production_steps.yaml, config/pipeline_coverage.yaml, docs/pipeline-audit.md, src/ytscout/audit.py, src/ytscout/cli.py (audit), tests/test_audit.py
**Milestone**: M3

## Why

"Manual hours per video" is the denominator of the only number that ranks niches. This
turns it from a guess into a measured fraction: which production steps the existing
pipeline does, which it half-does, which are still Nagz.

## Scope

- `config/production_steps.yaml`: the `DESIGN.md §6.4` table as data. One entry per step
  with `id`, `label`, `default_hours`, `notes`, and flags:
  ```yaml
  - id: research        # Topic research & fact-check
    label: ...
    default_hours: 0.75
  - id: script
    default_hours: 0.5
  - id: voiceover
    default_hours: 0.25
  - id: visuals_stock   # stock / AI images / clips
    default_hours: 1.0
    specific_footage_hours: 2.5
  - id: footage_original   # screen recording / gameplay / original footage
    default_hours: 3.0
    disqualifying_for_faceless: true
  - id: presenter
    default_hours: 4.0
    disqualifying_for_faceless: true
  - id: assembly
    default_hours: 1.0
  - id: thumbnail
    default_hours: 0.25
    shorts_hours: 0.0
  - id: metadata        # title / description / tags
    default_hours: 0.15
  - id: upload
    default_hours: 0.1
  - id: qa
    default_hours: 0.15
    floor_hours: 0.1
  - id: community
    default_hours: 0.1
  ```
- **Read the pipeline repo, read-only**, at the `Add dirs` path: its `README.md`,
  `scripts/*.py`, `.claude/skills/*/SKILL.md`, and `channel/`. Do not read its `.env` or
  `scripts/.secrets/`. Do not modify anything there.
- `config/pipeline_coverage.yaml`: for every step id, `coverage: automated | partial |
  manual`, `evidence: [relative paths in the pipeline repo]`, `notes`, and for `partial`
  a `manual_hours_override`. Add a top-level `audited_at`, `pipeline_commit` (from
  `git -C <path> rev-parse --short HEAD`), and `formats_supported: [shorts]` or
  `[shorts, longform]` based on what the assembler can actually produce.
- `docs/pipeline-audit.md`: a page for Nagz — what the pipeline does per stage, where the
  human is still in the loop, what it cannot do (long-form? thumbnails? original footage?),
  and the resulting `manual_hours_per_video` for a Short and for a long-form video that
  requires every non-disqualifying step.
- `ytscout audit`: loads both YAML files, validates that every step has a coverage entry
  and vice versa, prints a table (step, default h, coverage, effective h) and the two
  totals above. Exit 1 on a mismatch.
- `tests/test_audit.py`: both files load; ids match one-to-one; effective hours arithmetic
  for a hand-picked case; `audit` exits 1 when a step is missing (use a tmp copy).

## Out of scope

- Changing the pipeline. Using coverage in scoring (023 does).

## Acceptance criteria

- [ ] `.venv\Scripts\python.exe -m ytscout audit` exits 0 and prints the table.
- [ ] `pytest -q` passes, including `tests/test_audit.py`.
- [ ] `git -C C:\Users\nagaj\git\top-five-animals-1 status --porcelain` is unchanged by this
      session (nothing written there).
- [ ] `docs/pipeline-audit.md` names, for each of the 12 steps, the file(s) that justify
      the coverage call, or says "no evidence — assumed manual".
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §6.4, §11 M3`. Be conservative: when unsure whether a step is automated or
partial, choose `partial` with an override and explain. Nagz corrects the file in 020.
