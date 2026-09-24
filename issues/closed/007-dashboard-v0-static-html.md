# 007 — `dashboard`: static HTML from SQLite, no network

**Type**: AFK
**Blocked by**: 004
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/dashboard/{build.py,templates/*.html.j2,static/}, src/ytscout/cli.py (dashboard), tests/test_dashboard.py
**Milestone**: M1

## Why

The dashboard is the product. A file that opens from disk, every time, with no server and
no CDN, is what makes this tool survive months of neglect. Build the shell now; every
later issue adds a section.

## Scope

- `ytscout dashboard [--out dashboard/index.html]` renders one self-contained HTML file
  from the DB with Jinja2.
- Templates in `src/ytscout/dashboard/templates/`: `base.html.j2` (header, nav, inline
  CSS, inline JS) and one partial per section: `own.html.j2`, `competitors.html.j2`,
  `niches.html.j2` (placeholder), `findings.html.j2` (placeholder), `runs.html.j2`
  (placeholder). Sections render "nothing yet" when their tables are empty.
- Header: build time, own channel title + latest subs, quota units used today and
  yesterday (from `quota_ledger`), last run status (from `runs`).
- Own section: subs over time (line chart from `channel_snapshots`), last 20 videos table
  (title linking to `https://www.youtube.com/watch?v=…`, published, duration, latest
  views/likes/comments, Short?).
- Competitors section: **candidates** table (status NULL: title, subs, hit count,
  reasons, and Approve / Reject / Watch buttons that 008 wires up — render them disabled
  with a tooltip "start `ytscout serve`" until 008), **approved** table (status
  'approved'/'watch'; empty until 009).
- Charts: vendor **Chart.js** once. Download the pinned UMD build (`chart.umd.js`, current
  4.x) into `src/ytscout/dashboard/static/`, note version + MIT licence in
  `static/README.md`, inline its contents into the page at build time via a `<script>`
  block. If the download is blocked in the session, write the placeholder chart as an
  inline SVG sparkline instead and say so in the Outcome; do **not** link a CDN.
- Inline everything. The page must render from `file://` with the network unplugged.
- `tests/test_dashboard.py`: build against a tmp DB seeded by the 004 fixture; the output
  file exists; contains the own channel title and 5 video titles; contains **no**
  `src="http`, `href="http…css`, `@import`, or `fetch("http` — only `href="https://www.youtube.com/`
  links are allowed as external references (assert with a regex that whitelists that
  prefix); is under 5 MB.

## Out of scope

- The review server (008). Metrics (010). Niches (025). Findings (017).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_dashboard.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout dashboard` writes `dashboard/index.html` from the
      real DB (or an empty one) and exits 0.
- [ ] The external-reference test passes: no CDN, no remote CSS/JS/fonts.
- [ ] Unverified by the session (say so): it looks right in a browser. Nagz checks in 009.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §9.1` — static HTML chosen over a server on purpose. Keep the CSS plain and
readable; this will be edited by hand later. `dashboard/index.html` is gitignored (it is
generated); the templates are not.

## Outcome (closed 2026-09-24)

Delivered: `ytscout dashboard [--out PATH]`, which writes `dashboard/index.html` by
default. The code is in `src/ytscout/dashboard/build.py`, which has `load()` for reading,
`render()`, and `build()`. The templates are `templates/base.html.j2` plus one partial
per section, and there is a new `store.read_copy()`. Chart.js **4.5.1** (`chart.umd.js`,
MIT) is vendored in `src/ytscout/dashboard/static/`, with its licence and a `README.md`
that records the version, the source tarball and the SHA-256. It was downloaded from the
npm registry, so the SVG fallback was not needed. There are 9 tests in
`tests/test_dashboard.py`, and the suite now has 151 passing tests. No API units or
`claude -p` calls were spent.

How each criterion was checked, by running it:

- `pytest -q` passes. The DB is seeded by running `collect_own` over the 004 fixtures
  with `FakeTransport`, plus some hand-added candidates, an approved channel, a rejected
  channel, ledger rows and a run. The tests check that the file exists, that it is
  under 5 MB, that it contains the own channel title and all 5 fixture video titles
  (with 5 watch links), the header numbers, the candidate order, the approved/rejected
  split, the HTML escaping, and that an empty DB shows "Nothing yet" in every section.
- External references: `assert_self_contained` checks every `src=`, `href=`, `action=`,
  `poster=`, `data=` and `url(` that points off the machine (`http:`, `https:` or `//`).
  Only `href="https://www.youtube.com/watch?v=<id>"` is allowed through. It also checks
  for `src="http`, remote `.css` hrefs, `@import` and `fetch("http`.
  `test_the_whitelist_catches_a_cdn` injects a CDN script, a Google Fonts link, an
  `@import` and a `youtube.com.evil.example` link, and asserts that each one fails.
- `.venv\Scripts\python.exe -m ytscout dashboard` against the real DB exited 0. It wrote
  216,520 bytes: 7 own videos, 0 candidates (a real `discover` has not run yet) and
  0 approved. The only external references in the output are 7 `watch?v=` links. The
  real `ytscout.sqlite` kept the same size and mtime (only the `-shm` reader file was
  touched).
- `ruff check .` and `ruff format --check .` are clean. `ytscout --help` lists
  `dashboard`. The command has no `--dry-run`, because it makes no API calls and never
  writes the DB.
- **Unverified: how it looks in a browser.** I did not open the page. I checked the
  structure (the canvas, the JSON data block and the inlined Chart.js are all present),
  not the rendering. Nagz checks it in 009.

Decisions, and why:

- **Read-only by construction.** `store.read_copy(path)` opens the DB with `mode=ro`,
  copies it into memory with `sqlite3.backup`, and migrates the copy. The real DB is
  never written or migrated. That matters here because it may still be at schema 0001:
  `discovery_json` arrives with 0002 on the first real `discover`. If the DB is missing,
  the page is built empty and `data/` is not created.
- **The own channel is the `channels` row with `role='own'`,** so the dashboard does not
  need `config/settings.yaml`. It still uses the settings for the DB path when they are
  there.
- **Candidates are `role='competitor' AND status IS NULL`,** sorted by
  `discovery_json.score` descending, then by title. This page leaves out the issue-006
  hint's `discovery_json IS NOT NULL` filter, so that a competitor added by hand still
  shows up. The approved table shows `approved` and `watch`, and rejected channels never
  appear.
- **The review buttons** are `<button disabled title="start `ytscout serve`"
  data-decision="approved|rejected|watch">`, and each row carries
  `data-channel-id`. That is the hook 008 uses to wire them.
- **Chart data** goes in `<script type="application/json" id="subs-data">` through
  Jinja's `tojson`, which escapes `<`, `>` and `&`. The build strips Chart.js's
  `//# sourceMappingURL=` line so that devtools never request a `.map` file, and it
  refuses to inline a file that contains `</script`. Autoescape is on and undefined
  variables are strict.
- **The CSS** is plain, uses custom properties, supports light and dark mode, and needs
  no web fonts.

Notes for the next issues:

- **008:** make the buttons live when the page is served from `ytscout serve`, for
  example by removing `disabled` when `location.protocol` is `http:`. Alternatively,
  render a `serve_mode` flag from the server.
- **009:** Chart.js is committed with CRLF normalisation on this checkout, so the
  on-disk SHA-256 may not match `static/README.md`, which records the LF bytes of the
  upstream file. The inlined script works either way.
- **Placeholders:** the niches section is for 025 and the findings section for 017. The
  runs placeholder names no issue; later work can list recent `runs` rows there.
