# 006 — `discover`: find candidate competitors from the own channel's own titles

**Type**: AFK
**Blocked by**: 004
**Add dirs**: none
**Model**: claude-opus-5-5 medium
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

## Outcome (closed 2026-09-24)

Delivered: `ytscout discover [--max-searches N] [--max-units N] [--dry-run]`. The code is
in `src/ytscout/text.py` (tokens, a 100-word `STOP_WORDS`, `seed_queries`,
`parse_keywords`, `query_terms`) and `src/ytscout/collect/discover.py` (the run, plus the
pure filter functions `size_band`, `screen_channel` and `screen_format`). Migration
`0002_discovery.sql` adds `channels.discovery_json`. `config/scoring.yaml` gains a
`discovery:` section, parsed by `scoring.discovery_config`. Tests are in
`tests/test_discover.py` (22). The suite has 143 passing tests. No real API calls were
made.

How each criterion was checked, by running it:

- `pytest -q` passes. `test_seed_queries_from_the_ten_titles` asserts the 10 exact query
  strings. `test_filter_keeps_exactly_the_two_good_channels` asserts that only
  `UCgood1` and `UCgood2` are kept, and that each dropped channel has a reason: own
  channel, rejected before, a size band miss for both big channels, and a Shorts share of
  0 % for the long-form one. `test_rejected_channel_never_reappears_and_rerun_is_idempotent`
  runs discovery twice and checks there are no duplicate rows, that snapshots double,
  and that the rejected row is untouched.
- `discover --dry-run` against the real DB (7 own titles) printed 6 queries and
  "planned: 12 searches (1200 units) + 1 own channels.list = 1201 units", with a worst
  case of 1,225. The DB file's mtime was unchanged. `test_cli_dry_run_plans_default_searches_and_writes_nothing`
  checks the DB bytes are unchanged. With the 10 fixture titles the plan is 16 searches
  and 1,601 units.
- The fixture run costs **203 units**: 2 searches × 100, 2 channels calls (own and the
  candidate batch) and 1 videos call. `test_units_are_searches_times_100_plus_lookups`
  asserts this, and asserts it is under 2,300.
- `--max-searches 30` exits 1 with a message. Running with neither `--max-units` nor
  `--dry-run` exits 1.
- `ruff check .` and `ruff format --check .` are clean.

Decisions, and why:

- **Channel tags come from `brandingSettings.channel.keywords`, not `channels.snippet`,**
  because the snippet has no tags field. This takes one extra `channels.list` call on
  the own channel (`part=statistics,brandingSettings`, 1 unit). The same call gives the
  current own subscriber count. If it returns nothing, the latest own snapshot is used.
  `DataApi.channels()` now takes `part=`. Keywords are parsed with `shlex`, because
  multi-word tags come quoted. A tag with no content word left (`shorts`) is skipped.
  Tags are used lower-cased with no `top 5 ` prefix.
- **An n-gram is counted once per title,** so one repetitive title cannot make a query on
  its own. 2-grams join tokens that are adjacent after stripping. Ties go to the first
  sighting in newest-first title order. `#shorts` and numerals are stripped along with
  the listed tokens.
- **The screen runs in two stages to save units.** The channel-level checks come first:
  own channel, previously rejected, size band and keyword overlap. Keyword overlap is
  measured on the search-hit titles against the words of all the queries. Then
  `videos.list` fetches durations only for channels still standing, and the Shorts-share
  check follows. Channels dropped early therefore have no Shorts reason. A hidden
  subscriber count is a drop. If the own subscriber count is unknown, the small band
  `[0, 10,000]` is used.
- **`--max-units` trims the queries so the worst case fits.** The worst case is
  `worst_case_units(q) = 1 + 102 × searches`. A run therefore never stops on its own cap
  after spending the searches. `--max-units 300` allows 1 query, and anything under 205
  exits 1. The daily cap or Google can still stop a run. When that happens it exits 3,
  writes nothing for unjudged channels, and marks the `runs` row `quota_exhausted`
  (`runs.kind = 'discover'`).
- **`discovery_score` is the `score` key inside `discovery_json`,** not a separate
  column. The dashboard can use `json_extract(discovery_json, '$.score')`. The reasons
  for kept channels are stored as well, for example `subs 5,000 within [120, 12,000]`.
- **Existing channels keep their `role` and `status` on a re-run,** because
  `upsert_channel` never changes them. An approved competitor rediscovered later stays
  approved and gets a new snapshot and updated `discovery_json`. A channel dropped on a
  later run keeps its old `discovery_json`.
- **Dry run:** titles, rejected ids and own subs are read from the real DB opened
  `mode=ro`, and the rest of the run happens on the in-memory DB. `DryRunTransport`
  returns no hits, so the plan lists the own-channel call and the searches, and states
  the worst case for the lookups.

For the next issues:

- **009 (real run):** set `HTTPLIB2_CA_CERTS` as in 005 until 033 is done. The real DB
  has only 7 own titles, which give 6 queries (12 searches, ≤ 1,225 units). They include
  weak ones such as `top 5 vs` and `top 5 viral versus`. Run `collect --own` first so
  that more titles exist, or cap with `--max-searches`. The first real `discover` also
  applies migration 0002.
- **007/008:** order candidates with `role='competitor' AND status IS NULL AND
  discovery_json IS NOT NULL`, by `json_extract(discovery_json,'$.score')` descending.
