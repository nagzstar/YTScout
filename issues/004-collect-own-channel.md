# 004 — `collect --own`: first vertical slice through the API into SQLite

**Type**: AFK
**Blocked by**: 003
**Add dirs**: none
**Covers**: src/ytscout/collect/{__init__,own}.py, src/ytscout/cli.py (collect), tests/test_collect_own.py, tests/fixtures/own_channel/
**Milestone**: M0

## Why

The first command that goes API → client → store end to end. Once this works against a
fake transport, 005 runs it for real and the project has its first rows.

## Scope

- `ytscout collect --own [--videos N] [--max-units N] [--dry-run]`.
  1. `channels(own_channel_id)` → `upsert_channel(role='own')`, `add_channel_snapshot`,
     store `uploads_playlist_id`.
  2. `playlist_items(uploads)` page by page until `--videos` (default 200) ids collected
     or the playlist ends.
  3. `videos(ids)` in batches of 50 → `upsert_video` (title, description, tags,
     published_at, duration_s, category_id) and `add_video_snapshot` (views, likes,
     comments).
  4. `is_short = duration_s <= shorts_max_seconds` with `shorts_max_seconds: 180` read
     from `config/scoring.yaml` (create the file with just that key; 023 fills the rest).
     `DESIGN.md §5.3` mentions classifying by the channel's format mix; that refinement is
     for later — say so in a code comment.
  5. Print a one-line summary: channels, videos, snapshots written; units used this run;
     units used today.
- `--dry-run` swaps in `DryRunTransport`, prints the planned calls and their unit costs,
  writes nothing to the DB.
- `--max-units` sets the ledger's `run_cap`. Without it the command refuses to run and
  prints why (the guard enforces the same rule for sessions; the CLI enforces it for
  everyone). `scripts/run_weekly.ps1` will pass `--max-units 8000`.
- `QuotaExhausted` mid-run: commit what was written, print how far it got, exit **3**.
- Every subcommand that touches the DB records a `runs` row (start, finish, kind, status).
  Put that in a small context manager in `cli.py` so later commands get it for free.
- Fixtures: `tests/fixtures/own_channel/` — one channel, one playlist page of 5 items,
  one videos batch of 5, including one video of 45 s, one of 200 s, one with no tags.
- Tests: run the subcommand in-process against `FakeTransport` and a tmp DB; assert 1
  channel, 5 videos, 5 snapshots, `is_short` correct for 45 s and 200 s, units charged =
  1 + 1 + 1; `--dry-run` writes no rows; missing `--max-units` exits non-zero.

## Out of scope

- A real API call. No key exists yet — 005 does that.
- Competitors (006) and Analytics (011).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_collect_own.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --own --dry-run` prints three planned
      calls with costs 1, 1, 1 and touches no DB file (check `data/` afterwards).
- [ ] `.venv\Scripts\python.exe -m ytscout collect --own` (no `--max-units`) exits non-zero
      with a message that names the flag.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §8.1` rules. Keep `collect/own.py` small; 006 and 010 will reuse the
"playlist → videos → snapshots" walk, so put that walk in `collect/walk.py` as a function
that takes a channel row and returns counts.
