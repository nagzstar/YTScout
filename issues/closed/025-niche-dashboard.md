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

## Outcome (closed 2026-09-25)

Delivered: the Niches section of `dashboard/index.html`, built from the latest `niche_scores`
row per niche.

- **Toggle** Shorts / Long-form (plain JS, Shorts default; each button shows its count). Each
  table is sorted by `score` desc: label (with a ▸ expand button), category, **score
  (£/manual h)**, opportunity, est £/month as `£p50 (£p25–£p75)`, manual h/month, flags as
  badges (every flag in `confidence_flags_json`, including `longform_uncalibrated` and
  `no_small_channels`), trend arrow, status, and **Track** / **Shelve** buttons carrying
  `data-kind="niche" data-id=<niche id> data-decision="track|shelve"` (008's POST /decide
  already accepts them).
- **Numbers** to 2 significant figures (new `sig2` filter); the cell's `title` carries the
  full value (6 significant figures). A disqualified niche's NULL hours render as ∞.
- **£ band**: p25/p75 views ÷ 1000 × `rpm_gbp`, the same formula as `est_monthly_gbp` (p50).
- **Trend**: latest `opportunity` vs the newest row at least 28 days older than the latest
  row's `scored_at`. ▲ at ≥ +0.05, ▼ at ≤ −0.05 (both inclusive), ▬ otherwise, "—" when there
  is no such row.
- **Filter** "opportunity ≥" (default 0) hides rows, and their open sample rows, below it.
- **Expanded row**: the niche's small channels (channel links) with their outlier videos (watch
  links, views), recomputed with `scoring.opportunity` at the row's `scored_at`. Also the
  queries, and the `required_steps` with each step's coverage (automated / partial / manual /
  "not in the audit").
- **Waiting** table (muted): niches with no score row yet, with format, category and status.
- **Header**: "N niches scored · M tracking · K shelved". `track`/`tracking` count as
  tracking and `shelve`/`shelved` as shelved, because the stored decision values are
  `track`/`shelve`. The `dashboard` CLI summary line also prints "N niches scored".

Decisions:

- The sample needs `config/scoring.yaml` (small/outlier rules) and `pipeline_coverage.yaml`.
  They reach the builder as `NicheContext`, from `dashboard.niche_context(root)`, and both
  `ytscout dashboard` and `ytscout serve` pass it. If either file is missing or invalid, only
  that part of the sample is dropped, with a note ("did not load" / "coverage unknown"). The
  build never fails over it. `build`/`load` without a context still work.
- To link outliers, `VideoSample` gained `video_id` (default None, `compare=False`), which
  `build_sample` fills. The scorers never read it, and all 023/024 tests pass unchanged.
- A click from file:// shows the "start `ytscout serve`" hint in the clicked section's own
  note (`#niche-serve-note`), not only in the competitors one.

Checked: `pytest -q` passes, 439 tests. The new tests are `test_sig2`, the trend rules, and
load/render against the 023 example scored through `score_niches`, plus an older example row
(▲), a higher-scoring long-form niche (tracking), a disqualified+uncalibrated Shorts niche
(shelved) and a proposed one (waiting). The Shorts first row is the example, the long-form
first row is the other niche, the badges render, there are 6 niche buttons with
`data-kind="niche"`, and the page is self-contained. `test_cli_dashboard_renders_niches` runs
`main(["dashboard"])` on a temp repo with that fixture DB. It exits 0 and the sample carries
watch links and coverage read from the root's config. `ruff format`/`ruff check` are clean.
`node --check` passes on the page's inline scripts. Run on the real repo, `python -m ytscout
dashboard` exited 0 with "0 niches scored", because the real DB has no scored niches yet.

**Unverified**: legibility in a browser, and the toggle/filter/expand behaviour when clicked.
No browser was driven. Nagz checks this in 027.

For 026/027/028: nothing changes in the data model. A snowball niche shows up in the waiting
table until it is scored.
