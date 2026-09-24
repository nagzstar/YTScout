# 004 — `collect --own`: first vertical slice through the API into SQLite

**Type**: AFK
**Blocked by**: 003
**Add dirs**: none
**Model**: claude-opus-5-5 medium
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

## Outcome (closed 2026-09-24)

Delivered: `ytscout collect --own [--videos N] [--max-units N] [--dry-run]`, working end to
end against `FakeTransport`. The code is in `src/ytscout/collect/{__init__,own,walk}.py`,
`src/ytscout/scoring/{__init__,config}.py`, the new `config/scoring.yaml` (only
`shorts_max_seconds: 180`), `start_run`/`finish_run` in `store/repo.py`, and the
`recorded_run()` context manager in `cli.py`. Tests are in `tests/test_collect_own.py` (8)
and the fixtures in `tests/fixtures/own_channel/`: one channel, one playlist page of 5
items and one videos batch of 5 (45 s, 200 s, a 59 s video with no tags, exactly 180 s,
and 30 s). The suite has 123 passing tests. No real API calls were made.

How each criterion was checked, by running it:

- `pytest -q` passes. `test_collect_own_writes_channel_videos_and_snapshots` asserts 1
  channel, 5 videos and 5 video snapshots, that 45 s is a Short and 200 s is not, and that
  the ledger holds 3 units from the calls channels → playlistItems → videos.
- `collect --own --dry-run` against the real repo printed three planned calls at 1 unit
  each ("planned: 3 calls, 3 units"), and `data/` was still empty afterwards.
  `test_dry_run_writes_nothing` and `test_dry_run_leaves_an_existing_db_untouched` check
  the same thing repeatably.
- `collect --own` with no `--max-units` exits 1 and the message names `--max-units`. This
  was proved by `test_missing_max_units_refuses` (in-process) and
  `test_collect_own_without_max_units_refuses_via_subprocess`. I did not type the bare
  command in the shell, because `lazyboy/guard.py` blocks it for sessions, as intended.
- `ruff check .` and `ruff format --check .` are clean. `ytscout --help` runs.

Decisions, and why:

- **The walk is `walk_uploads(api, conn, channel, *, limit, shorts_max_seconds,
  counts=None) -> Counts`** in `collect/walk.py`. `channel` is a `channels` row or any
  mapping with `id` and `uploads_playlist_id`. `store_video()` sits beside it for 006 and
  010 to reuse. `Counts` keeps channel snapshots and video snapshots apart, and
  `.snapshots` gives their sum.
- **A quota stop comes back as a value, not an exception.** The walk and `collect_own`
  catch `QuotaExhausted` and put it in `Counts.stopped`. Each videos batch commits in its
  own `with conn:`, and the channel upsert and snapshot commit before the walk starts. On
  a stop the CLI prints the summary plus the reason and exits 3, and the `runs` row is
  marked `quota_exhausted`. `test_quota_exhausted_mid_run_commits_and_exits_3` covers
  this with `--max-units 2`.
- **A dry run takes the real code path on an in-memory DB.** That DB is seeded from the
  real DB's `quota_ledger`, which is opened with `mode=ro`, so the daily cap is still
  honoured and `data/` is never created or migrated. `DryRunTransport` answers every
  call with no items, so under a dry-run ledger the walk plans with placeholder ids
  (`<uploads playlist>`, `<video ids from the playlist>`). That way the plan shows the
  videos call too. The plan also prints the upper bound for `--videos N`: 1 + 2 ×
  ceil(N/50) units, which is 9 for the default of 200. A dry run writes no `runs` row.
- **With no `config/settings.yaml`, only a dry run proceeds**, using the placeholder id
  `<own_channel_id>`. A real run exits 1 and says what is missing. This repo has no
  `settings.yaml` yet.
- **`--own` is a flag.** `collect` without a source exits 1. 006 and 011 add
  `--competitors` and `--analytics` next to it. `collect` is no longer in `STUBS`, and
  `COMMANDS` now lists it second, after `doctor`.
- **The test seam is `cli.make_transport(settings, dry_run)`**, which tests monkeypatch.
  A real run builds `GoogleTransport(settings.api_key)`. A missing key exits 1 with the
  transport's message.
- **`is_short` uses an inclusive cut-off:** `duration_s <= 180`. A code comment in
  `walk.store_video` says the channel format-mix refinement from DESIGN §5.3 comes later.
  `scoring.yaml` is required: if it is missing or malformed, the command exits 1.
- **A video with no tags stores `tags_json = "[]"`, not NULL**, because the API leaves the
  key out when there are no tags. `categoryId` and `description` are stored when present.
- **`runs` statuses are `ok`, `quota_exhausted` and `error`.** An exception escaping
  `recorded_run` marks the row `error` and re-raises. `runs.kind` is `collect_own`.

For 005: create `config/settings.yaml` with `own_channel_id` and put `YT_API_KEY` in
`.env`. Then `collect --own --max-units 20` costs 1 + ceil(n/50) × 2 units. For about 200
uploads that is 9. Collecting fewer uploads than `--videos` asks for is normal when the
playlist is shorter. Only the ETag cache would make a second run look free, and it isn't:
every call is still charged.
