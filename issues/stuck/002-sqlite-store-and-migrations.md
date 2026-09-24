# 002 — SQLite store, migrations, append-only snapshots

**Type**: AFK
**Blocked by**: 001
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/store/{__init__,db,repo}.py, src/ytscout/store/migrations/0001_initial.sql, tests/test_store.py
**Milestone**: M0

## Why

Every collector writes here and every scorer reads here. Getting the schema and the
append-only rule right now means 12 months of history accumulates from the first run
instead of being overwritten by the second.

## Scope

- `store/db.py`: `connect(path: Path) -> sqlite3.Connection`. Creates the parent dir,
  opens with `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `row_factory=sqlite3.Row`, and
  applies every `migrations/NNNN_*.sql` not yet recorded in `schema_migrations(version,
  applied_at)`, in order, each in a transaction. Idempotent.
- `migrations/0001_initial.sql`: every table in `DESIGN.md §10` with the columns listed
  there, plus:
  - `channel_metrics(channel_id, computed_at, window, format, metrics_json)` (010 fills it)
  - `api_cache(key PRIMARY KEY, etag, body_json, fetched_at)` (003 uses it)
  - `collector_state(kind, key, done_at, PRIMARY KEY(kind, key))` (032 uses it)
  - `decisions(id, kind, target_id, decision, decided_at)` (008 uses it)
  Indexes: `channel_snapshots(channel_id, captured_at)`, `video_snapshots(video_id,
  captured_at)`, `videos(channel_id, published_at)`, `niche_scores(niche_id, scored_at)`.
  `own_analytics` uses `window_start, window_end` instead of `day` (011 explains why).
- `store/repo.py`: thin typed helpers, no ORM: `upsert_channel`, `add_channel_snapshot`,
  `upsert_video`, `add_video_snapshot`, `latest_channel_snapshot`,
  `latest_video_snapshot`, `videos_for_channel(channel_id, since=None)`,
  `set_channel_status`. **No helper updates or deletes a snapshot row.** That rule is the
  point of this issue.
- All timestamps stored as ISO-8601 UTC strings (`YYYY-MM-DDTHH:MM:SSZ`). One helper
  `now_utc()` in `store/db.py`; nothing else calls `datetime.now`.
- `settings.data_dir / "ytscout.sqlite"` is the default path; `connect()` takes an
  explicit path so tests use `tmp_path`.
- Tests: fresh DB has exactly the expected table set; `connect()` twice applies nothing
  new; channel/video/snapshot round-trip; two `add_video_snapshot` calls for one video
  produce two rows; `repo` has no function whose name starts with `update_` or `delete_`
  that touches a `*_snapshots` table (assert by introspection).

## Out of scope

- Any collector. No network.

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_store.py`.
- [ ] `python -c "from ytscout.store.db import connect; connect(Path('data/x.sqlite'))"`
      creates the file with `schema_migrations` at version 1; running it again changes
      nothing (`sqlite3 data/x.sqlite "select count(*) from schema_migrations"` = 1).
      Delete `data/x.sqlite` afterwards.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §10` is the schema; the additions above are the only deviations and should be
reflected there in the Outcome (say what you added and why). Keep the SQL plain — no
SQLAlchemy.


## Lazyboy

Parked after two attempts: closed=False committed=False clean=True pushed=True subtype=success error=True. Last result: API Error: 400 Claude Code 2.1.267 does not support this model; version 2.1.280 or newer is required. Run 'claude update', or update the Claude desktop app, then try again.
