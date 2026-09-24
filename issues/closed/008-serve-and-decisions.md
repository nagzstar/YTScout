# 008 — `serve`: approve / reject / watch from the dashboard

**Type**: AFK
**Blocked by**: 007
**Add dirs**: none
**Model**: claude-opus-5-5 medium
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

## Outcome (closed 2026-09-24)

Delivered `ytscout serve [--port 8765]` in `src/ytscout/dashboard/serve.py`. It is a
stdlib `HTTPServer` bound to `127.0.0.1` and never to anything else. It serves the
`dashboard/` directory as static files, answers `GET /health` with
`{"ok": true, "db": "<path>"}` and handles `POST /decide`. On start it rebuilds the
dashboard so that `/` is never a 404, prints the URL, and on Ctrl-C prints `serve: stopped`
and exits 0.

What I checked, by running it:

- `pytest -q` passes: 164 tests. `tests/test_serve.py` runs the server in a thread on
  port 0. It asserts that the bound address is `127.0.0.1`, POSTs three channel
  decisions, and checks the DB statuses, the `decisions` rows, the JSONL lines and that
  the `index.html` mtime advanced. It also checks that a niche decision sets
  `niches.status`, that seven kinds of bad request return 400 or 404 and write nothing,
  that a foreign Origin or Host gets 403 and a non-JSON body gets 415, and that the CLI
  path prints the URL and stops cleanly on Ctrl-C (simulated). I never ran
  `ytscout serve` itself.
- `grep -n "0.0.0.0" src/ytscout/dashboard/serve.py` finds nothing. A test asserts
  the same.
- `tests/test_dashboard.py` asserts that every button carries `data-kind`, `data-id`
  and `data-decision` and is no longer `disabled`.
- `ruff check` and `ruff format --check` are clean, and `--help` and `serve --help` run.

Decisions, and why:

- **One transaction per click.** `repo.record_decision` sets the status and inserts the
  `decisions` row. The JSONL line is appended inside the same transaction, before it
  commits. A failed write therefore rolls back, and no status can change without an
  audit line.
- **Where the audit log lives.** `data/decisions.json` is written next to the DB
  (`db_path.parent`), so it follows a custom `data_dir`.
- **Which decisions are accepted.** For a channel: `approved`, `rejected` or `watch`,
  which is the schema's CHECK. For a niche: `track` or `shelve`, with an integer id.
  Anything else is a 400. An unknown target is a 404, which the issue did not specify;
  I chose it.
- **Single-threaded server.** Only one writer can touch the DB at a time.
- **CSRF and DNS rebinding.** Even without auth, `/decide` requires
  `Content-Type: application/json`, which forces a CORS preflight that the server never
  answers. It also refuses a Host or Origin other than `127.0.0.1:<port>` or
  `localhost:<port>`. The reason: any website open in the same browser could otherwise
  POST to localhost.
- **Button behaviour.** The buttons are enabled. A click on a `file://` page, or a failed
  fetch, fills the inline `#serve-note` with "start `ytscout serve` and open
  http://127.0.0.1:8765/", and there is no `alert()`. A non-ok response shows the
  server's error. Success reloads the page. Every response carries
  `Cache-Control: no-store`.

Not verified: **the clicks in a real browser.** I never opened the page, so the note,
the reload and the button disabling are unchecked by eye. That is for 009.

For later issues:

- **Niche buttons:** 024/025 add the niche rows. Render the buttons as
  `data-kind="niche" data-id="{{ n.id }}" data-decision="track|shelve"`. The click
  handler in `base.html.j2` is delegated, so any such button works with no JS change.
- **Undoing a decision:** there is no endpoint for it. To reverse one, set the status
  by hand; the audit line tells you what to reverse.
