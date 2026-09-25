# 037 — Review page: "Hits" tooltip and serve picks up code changes

**Type**: AFK
**Blocked by**: 009
**Add dirs**: none
**Model**: claude-haiku-4-5-20251001
**Covers**: src/ytscout/dashboard/templates/competitors.html.j2, dashboard/serve.py
**Milestone**: M1

## Why

Two small complaints from the 009 review. The **Hits** column is not self-explanatory
(it means "search hits", the number of `search.list` results that pointed at the channel).
And `serve` has to be restarted after any code or template change, which every session
in 009 forgot at least once.

## Scope

- Hits column header gets a `title` attribute: "Search hits: how many discovery results
  pointed at this channel". Any other header that is not obvious gets the same treatment.
- `serve` rebuilds the dashboard from the current templates on every GET (it already
  rebuilds after a decision), so a template edit shows on refresh. Python code changes
  still need a restart; say so in `serve --help`.

## Out of scope

- Live reload of Python. Any layout changes.

## Acceptance criteria

- [ ] The rendered header carries the tooltip (assert in the build test).
- [ ] Editing the template and refreshing the page shows the change without restarting.

## Notes

Complaints are listed in the 009 Outcome.

## Outcome (closed 2026-09-25)

**Delivered:**
- Hits column header now carries `title="Search hits: how many discovery results pointed at this channel"` in the rendered HTML (verified by test in test_dashboard.py)
- serve now calls rebuild() on every GET request, so editing templates and refreshing shows the change immediately (verified by test in test_serve.py)
- serve --help updated to state: "templates reload on refresh, Python code changes need restart"

**Verification:**
- ✓ Added assertion in test_dashboard.py::test_candidates_sorted_by_score_and_approved_split to check tooltip is rendered
- ✓ Added test_serve.py::test_get_rebuilds_dashboard_on_every_request to verify GET rebuilds
- ✓ All 532 tests pass
- ✓ Help text shows new message
- ✓ ruff format and ruff check pass

**Decisions:**
- Only added tooltip to "Hits" column; other headers (Channel, Status, Subscribers, Why suggested, Review) are self-explanatory
- rebuild() on GET is wrapped in try/except so template errors don't prevent serving the existing cached dashboard
- No live reload of Python code (out of scope); requires manual restart as noted in help text
