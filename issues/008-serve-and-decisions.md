# 008 — `serve`: approve / reject / watch from the dashboard

**Type**: AFK
**Blocked by**: 007
**Add dirs**: none
**Covers**: src/ytscout/dashboard/serve.py, src/ytscout/store/repo.py (decisions), templates (buttons + JS), data/decisions.json, tests/test_serve.py
**Milestone**: M1

## Why

Approval is the one human input the competitor module needs, and the niche module will
need the same buttons (track / shelve). Build it once, tiny, localhost-only.

## Scope

- `ytscout serve [--port 8765]`: stdlib `http.server` bound to **127.0.0.1 only**. Serves
  `dashboard/` as static files and handles `POST /decide` with JSON
  `{"kind": "channel"|"niche", "id": "...", "decision": "approved"|"rejected"|"watch"|"track"|"shelve"}`.
- On a decision: append a line to `data/decisions.json` (JSON Lines, append-only audit:
  `{ts, kind, id, decision}`), write a `decisions` row, update the target's `status`
  (`channels.status` or `niches.status`), rebuild the dashboard, respond `{"ok": true}`.
  Unknown kind/decision → 400 with a message.
- Dashboard buttons (in the templates from 007) become live: `fetch("/decide", …)` then
  reload. When the page is opened from `file://` the fetch fails; show an inline note
  "start `ytscout serve` and open http://127.0.0.1:8765/" — no `alert()`.
- `GET /health` → `{"ok": true, "db": "<path>"}` so a test and a person can check it.
- Print the URL on start. `Ctrl-C` stops it cleanly.
- `tests/test_serve.py`: start the server in a thread on port 0, POST three decisions,
  assert the DB status, the JSONL lines, and that `dashboard/index.html` was rebuilt
  (mtime advanced); a bad decision returns 400; the server binds only to 127.0.0.1
  (assert the bound address).

## Out of scope

- Auth, HTTPS, anything multi-user. One person, one machine.
- Niche rows to decide on (024/025 add them; the endpoint already accepts `kind=niche`).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_serve.py`, without the session running
      `ytscout serve` itself (the guard blocks it; test in-process on an ephemeral port).
- [ ] `grep -n "0.0.0.0" src/ytscout/dashboard/serve.py` finds nothing.
- [ ] Buttons in the built HTML carry `data-kind`, `data-id`, `data-decision` attributes
      (assert in `tests/test_dashboard.py`).
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §9.1`. `data/decisions.json` is gitignored with the rest of `data/`. The
audit line exists so a fat-fingered reject can be found and reversed by hand.
