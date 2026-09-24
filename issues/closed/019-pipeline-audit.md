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

## Outcome (closed 2026-09-24)

Delivered: `config/production_steps.yaml` (the twelve §6.4 rows as data, under a
top-level `steps:` list), `config/pipeline_coverage.yaml` (audited against pipeline
commit `67b4c1d`), `docs/pipeline-audit.md`, `src/ytscout/audit.py`, a real
`ytscout audit` command, and `tests/test_audit.py` (14 tests; 49 pass in all). ruff
format and check are clean. `ytscout audit` exits 0 and prints the table; with a tmp
copy of the coverage file missing a step it exits 1 naming the step (tested via
subprocess).

The numbers: 1.20 h manual per Short, 1.25 h per long-form deep dive, out of 3.25 h of
non-disqualifying defaults. Nearly half is visual sourcing.

Decisions, and why:

- **One coverage value per step, not per format.** Simpler for 023 to consume. The only
  format difference the model expresses is `shorts_hours` on thumbnail. The cost is that
  long-form QA and the per-segment review in `assemble_long.py` are undercounted; the doc
  says so and 020 is where Nagz decides whether that matters.
- **Arithmetic** (`audit.effective_hours`): automated 0; manual = the format's base
  hours; partial = override, capped at the base so a Short's thumbnail is 0 whatever
  the coverage; `floor_hours` last (QA never below 0.1); a format not in
  `formats_supported` is costed fully manual. Overrides above `default_hours` are
  rejected at audit time. Disqualifying steps appear in the table with a `*` and are
  excluded from both totals.
- **Conservative calls.** Every "Claude drafts, the human approves" stage (research,
  script, metadata, thumbnail) is `partial` with the approval time as the override, not
  `automated`. `qa` is `manual` even though `check_script.py` and `check_render.py` exist:
  they add gates without removing the human's end-to-end watch. `upload` is `partial`
  because only `approve_week.py`, run by the owner at a terminal, ticks the consent box
  `yt_upload.py` requires. `visuals_stock` is `partial` at 0.5 h because Storyblocks has
  no API and forbids bots, so the owner downloads every clip; this is the number to
  correct in 020.
- **`formats_supported: [shorts, longform]`.** `assemble_long.py` is complete (renders
  16:9 per segment, `--splice` joins), and research/script/package/produce all have deep
  dive sections and templates. Compilations (pipeline issue 040) and tournaments (042)
  are briefs only; long-form means "a deep dive" here.
- **`audit` does not load `config/settings.yaml`**, only `find_repo_root`, so it runs on
  this machine before issue 005 fills the settings in. It has no `--dry-run` because it
  never touches the network; the CLI test proves it rejects the flag.
- Evidence was gathered by two read-only sub-agents summarising `scripts/*.py` and the
  skills, then every load-bearing citation was re-read by line before use.

Checked: `git -C C:\Users\nagaj\git\top-five-animals-1 status --porcelain` shows the
same single pre-existing ` M videos/INDEX.md` before and after; nothing was written
there and `.env` / `scripts/.secrets/` were never opened.

Unverified: nothing needed a human. The hour figures themselves are judgement, which is
what 020 exists for.

For the next issues: 020 edits `config/pipeline_coverage.yaml` by hand and re-runs
`ytscout audit`; the loader rejects a partial entry without `manual_hours_override`, an
override above the default, and an unknown format. 023 should import `load_steps`,
`load_coverage` and `effective_hours` from `ytscout.audit` rather than re-reading the
YAML; `Step.specific_footage_hours` is on the dataclass but the audit totals do not use
it, that is the niche tagger's job. `docs/` now exists and is tracked.
