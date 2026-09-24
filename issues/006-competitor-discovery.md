# 006 — `discover`: find candidate competitors from the own channel's own titles

**Type**: AFK
**Blocked by**: 004
**Add dirs**: none
**Covers**: src/ytscout/collect/discover.py, src/ytscout/text.py, tests/test_discover.py, tests/fixtures/discover/
**Milestone**: M1

## Why

The competitor analyser is only as good as its competitor list, and hand-picking finds
only the channels Nagz already knows. This turns his own titles into searches and the
search hits into a ranked, filtered candidate list he approves in 009.

## Scope

- `ytscout discover [--max-searches N] [--max-units N] [--dry-run]`. Default
  `--max-searches 20` (2,000 units); the CLI refuses more than 25.
- Seed queries (`text.py`):
  - Take the own channel's last 50 titles from the DB. Lower-case, strip numerals, strip
    the tokens `top`, `5`, `five`, `countdown`, `shorts`, `#shorts`, and a stop-word list
    (ship a 100-word list in `text.py`).
  - Count 1- and 2-grams; keep the 8 most frequent 2-grams and 4 most frequent 1-grams
    with count ≥ 2. Each becomes a query prefixed with `top 5 ` (e.g. `top 5 dangerous
    animals`). Add the channel's `tags` (from `channels.snippet` — if empty, skip).
  - De-duplicate; cap at `--max-searches / 2` queries.
- For each query run `search(type=video, order=viewCount)` and `search(order=date)`,
  `published_after = now − 365 d`, `max_results=50`. Collect `(channel_id, video_id)`.
- `channels(ids)` for every distinct channel (≤ 50 per call). `videos(ids)` for every hit
  (≤ 50 per call) to get durations — `search.list` does not return them.
- Similarity filter, all thresholds in `config/scoring.yaml` under `discovery:`:
  - `size_band`: subs within `[own_subs / 10, own_subs × 10]`; if own subs < 1,000 use
    `[0, 10,000]`.
  - `shorts_share_min: 0.7`: fraction of that channel's hits with `duration_s <=
    shorts_max_seconds`.
  - `keyword_overlap_min: 2`: distinct query terms appearing in the channel's hit titles.
  - Exclude the own channel and any channel already `status='rejected'`.
- Write survivors with `role='competitor'`, `status=NULL`, and `discovery_json =
  {score, reasons, hit_count, matched_queries}` (add the column in a new migration).
  Store their hit videos and snapshots too — they are free now and 010 wants them.
  `discovery_score = hit_count × keyword_overlap`, for ordering in the dashboard.
- Re-running is idempotent: already-known channels get a new snapshot and an updated
  `discovery_json`, never a duplicate row.
- `--dry-run` prints the queries it would run and the unit total.
- Fixtures: `tests/fixtures/discover/` — own titles (10), two search responses, one
  channels batch (6 channels: 2 too big, 1 long-form, 1 rejected, 2 good), one videos batch.
- Tests: query derivation from the 10 titles yields the expected strings; the filter keeps
  exactly the 2 good channels and records a reason for each rejection; units charged =
  (searches × 100) + channel calls + video calls; rejected channel never reappears.

## Out of scope

- Running it for real (009). Approve/reject UI (007, 008).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_discover.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout discover --dry-run` prints ≤ 20 planned
      searches and a unit total ≤ 2,000 + overhead, and writes nothing.
- [ ] A test proves the total units for the fixture run, and it is under 2,300.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §4.3`. Discovery is monthly, not weekly (`§8.1`); the CLI does not schedule
itself. No real API call in this session — there is no budget line here on purpose.
