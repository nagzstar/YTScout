# 007 — `dashboard`: static HTML from SQLite, no network

**Type**: AFK
**Blocked by**: 004
**Add dirs**: none
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
