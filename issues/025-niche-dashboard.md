# 025 — Niches in the dashboard: ranked table, format toggle, track / shelve

**Type**: AFK
**Blocked by**: 008, 024
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/dashboard/templates/niches.html.j2, src/ytscout/dashboard/build.py, tests/test_dashboard.py
**Milestone**: M4

## Why

The ranking is the deliverable. It has to be readable at a glance, honest about its
confidence, and one click from "keep watching this" or "forget it".

## Scope

- Niches section, driven by the latest `niche_scores` row per niche:
  - Toggle **Shorts / Long-form** (plain JS, no library) — default Shorts.
  - Table sorted by `score` desc: label, category, **score (£/manual h)**, opportunity,
    est £/month shown as `p50 (p25–p75)`, manual h/month, flags as small badges
    (`low_confidence`, `uncalibrated`, `disqualified`), trend arrow, status, and buttons
    **Track** / **Shelve** (via `serve`, `kind=niche`).
  - Trend arrow: compare the latest `opportunity` with the newest row ≥ 28 days older;
    ▲ if +0.05, ▼ if −0.05, ▬ otherwise, "—" when no older row.
  - A filter input "opportunity ≥" defaulting to 0.
  - Expand a row → sample: the niche's small channels with their outlier videos as
    YouTube links, the queries used, `required_steps` and which the pipeline covers.
  - Proposed / validated (unscored) niches listed below in a muted "waiting" table with
    their status, so the pipeline's state is visible.
- Header gets a line: "N niches scored · M tracking · K shelved".
- Tests: build against a DB seeded with the 023 example plus a second, higher-scoring
  long-form niche → the Shorts table's first row is the example, the long-form table's
  first row is the other; badges render; buttons carry `data-kind="niche"`; no external
  references (existing test).

## Out of scope

- Snowball (026), sensitivity (028).

## Acceptance criteria

- [ ] `pytest -q` passes, including the new niche assertions in `tests/test_dashboard.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout dashboard` renders the section from the fixture
      DB and exits 0.
- [ ] Unverified by the session (say so): legibility in a browser. Nagz checks in 027.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §6.5` (what to show). Numbers to 2 significant figures in the table; the
tooltip can carry the full value.
