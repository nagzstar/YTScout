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

## Outcome (closed 2026-09-24)

Delivered: `src/ytscout/store/{__init__,db,repo}.py`, `migrations/0001_initial.sql`,
`tests/test_store.py` (27 tests; suite now 74 passing), `pyproject.toml` package-data so the
`.sql` files ship with the package, and `DESIGN.md §10` updated. Acceptance was proved by
running it: pytest passes; `connect(Path('data/x.sqlite'))` twice leaves
`schema_migrations` at one row, version 1 (checked with Python's `sqlite3`, because the
`sqlite3` CLI is not on PATH here), then `data/x.sqlite*` were deleted; `ruff check` and
`ruff format --check` are clean; `ytscout --help` runs. No subcommand changed, so there was
no `--dry-run` to run.

Additions to DESIGN §10, and why:

- The four tables the issue asked for (`channel_metrics`, `api_cache`, `collector_state`,
  `decisions`) plus `schema_migrations(version, applied_at)`, which `connect()` creates
  itself before applying migrations. `channel_metrics` also gets a surrogate `id` and an
  index on `(channel_id, computed_at)`.
- `own_analytics` is keyed by `(video_id, window_start, window_end)` instead of `day`.
- **Snapshot tables have a surrogate `id INTEGER PRIMARY KEY` and no uniqueness on
  `(…_id, captured_at)`**, so two snapshots in the same second are both kept. "Latest"
  orders by `captured_at DESC, id DESC`.
- **Append-only is enforced by the schema as well as by `repo`:** `BEFORE UPDATE` and
  `BEFORE DELETE` triggers on `channel_snapshots` and `video_snapshots` raise
  `'… is append-only'` (an `sqlite3.IntegrityError`). `niche_scores` and
  `channel_metrics` are append-by-convention, with no trigger.
- Foreign keys: snapshots → channels/videos, videos → channels, niche_channels and
  niche_scores → niches. `transcripts`, `own_analytics` and `video_summaries` have no FK,
  so they can be written before the video row exists.
- `niches` has `UNIQUE(format, topic)` because a niche is a `format × topic` pair.

Things the next issues should know:

- **Repo helpers never commit.** The caller owns the transaction (`with conn:` or
  `conn.commit()`), which lets 003/032 checkpoint a batch at once. A test covers this.
- `upsert_channel`/`upsert_video` use COALESCE, so a `None` argument keeps the stored
  value. `role` and `first_seen` are set on the first insert and never change after.
  `status` changes only through `set_channel_status`, which raises `LookupError` for an
  unknown channel (008 can rely on that). `tags` goes in as a list and is stored as
  `tags_json`. `is_short` is stored as 0/1.
- Timestamp arguments accept an ISO string (stored unchanged) or an aware `datetime`
  (converted with `to_utc_iso`; a naive one is refused). `now_utc()` in `store/db.py` is
  the only `datetime.now` in the package, and a test enforces that. 003's Pacific-day
  quota logic will need a second clock helper; add it to `store/db.py` and change the
  test if needed.
- `default_db_path(settings)` returns `settings.data_dir / "ytscout.sqlite"`.
- Also added: `get_channel` and `get_video`, which are read-only.
