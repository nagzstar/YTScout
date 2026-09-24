# 003 — Quota ledger and YouTube Data API client with swappable transport

**Type**: AFK
**Blocked by**: 002
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/youtube/{__init__,quota,client,transport}.py, tests/test_quota.py, tests/test_client.py, tests/fixtures/
**Milestone**: M0

## Why

The whole project lives inside 10,000 units a day. If the ledger is wrong, a Monday run
silently dies at 08:00 UK and the dashboard goes stale. If the client is not testable
without the network, every later issue needs real quota to test. Both get fixed here.

## Scope

- `youtube/quota.py`: `Ledger(conn, daily_cap, run_cap=None)`.
  - `today_pacific()` returns the `America/Los_Angeles` date (stdlib `zoneinfo`); the
    ledger row is `quota_ledger(day_pacific, units_used)`.
  - `charge(units: int)` raises `QuotaExhausted(kind="daily"|"run", used, cap)` if the day
    total or the run total would exceed its cap; otherwise adds and commits. Charge happens
    **before** the request. An invalid request still costs Google ≥ 1 unit, so it costs us.
  - `remaining_today()`.
- `youtube/transport.py`: `Transport` protocol with one method
  `call(resource: str, method: str, **params) -> dict`.
  - `GoogleTransport(api_key)` builds `googleapiclient.discovery.build("youtube", "v3",
    developerKey=key, cache_discovery=False)` lazily and executes. The key is
    `Settings.api_key` (`YT_API_KEY` in `.env`). An `HttpError` whose reason is
    `quotaExceeded` is re-raised as `QuotaExhausted(kind="google")`: the Cloud project may
    be shared with the production pipeline, whose uploads the ledger cannot see.
  - `FakeTransport(fixtures: dict[(resource, method, frozen params) -> response])` for
    tests; raises `KeyError` naming the missing call so a test tells you which fixture to
    add. Supports `nextPageToken` pagination by letting a fixture be a list of pages.
  - `DryRunTransport()` records `(resource, method, params, units)` and returns empty,
    well-formed responses (`{"items": []}`). `--dry-run` everywhere uses it.
- `youtube/client.py`: `DataApi(ledger, transport)` with methods that charge, then call:
  - `search(q, *, order, published_after, video_duration=None, max_results=50)` — 100 units
  - `channels(ids: list[str])` — 1 unit per call, ≤ 50 ids, parts `snippet,statistics,contentDetails`
  - `playlist_items(playlist_id, *, page_token=None)` — 1 unit per page, 50 per page
  - `videos(ids: list[str])` — 1 unit per call, ≤ 50 ids, parts `snippet,statistics,contentDetails`
  - Each method's cost is a module constant in one table; the client never hard-codes a
    number inline.
  - ETag: `api_cache` keyed by resource+method+params; send `If-None-Match`; on 304 return
    the cached body. Charge still applies (YouTube charges either way). Note this in a
    docstring so nobody "optimises" the charge away.
  - `parse_duration("PT1M3S") -> 63`; `parse_dt("2026-01-02T03:04:05Z") -> datetime`.
- Tests: charges match the cost table; `QuotaExhausted` at the daily cap and at the run
  cap; day rollover uses Pacific midnight (freeze time with a monkeypatched clock);
  pagination through two fixture pages; `DryRunTransport` never calls the network;
  `parse_duration` on `PT45S`, `PT2M`, `PT1H2M3S`, `P0D`.
- Fixtures in `tests/fixtures/data_api/*.json`: hand-written minimal responses (a channel,
  a playlist page with 5 items, a videos batch of 5). Small and readable; not recordings.

## Out of scope

- Any real call. No API key exists yet (`YT_API_KEY` is empty until issue 005).
- The Analytics API (011).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_quota.py` and `tests/test_client.py`.
- [ ] A test proves `DataApi.search` charges 100 and `videos` with 50 ids charges 1.
- [ ] A test proves the 9,001st unit in a Pacific day raises `QuotaExhausted(kind="daily")`
      and the ledger row still reads 9,000.
- [ ] A test proves `run_cap=300` stops a run at 300 while the day is well under cap.
- [ ] A test proves a `quotaExceeded` `HttpError` from the transport surfaces as
      `QuotaExhausted(kind="google")`.
- [ ] `grep -rn "googleapiclient" src/` shows it imported only in `youtube/transport.py`.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §8.1` for costs and rules. Quota resets at midnight Pacific, which is 08:00 in
the UK; the ledger is per Pacific day, never per UK day.

## Outcome (closed 2026-09-24)

Delivered: `src/ytscout/youtube/{__init__,quota,transport,client}.py`, `tests/test_quota.py`
(11 tests), `tests/test_client.py` (31 tests; the suite is now 116 passing), and four
hand-written fixtures in `tests/fixtures/data_api/`: a channel, two playlist pages (5 + 2
items, so pagination has something to follow) and a videos batch of 5. Every acceptance
criterion was checked by running it. The pytest names are `test_search_charges_100`,
`test_videos_with_50_ids_charges_1`, `test_the_9001st_unit_raises_daily_and_the_row_stays_at_9000`,
`test_run_cap_300_stops_the_run_while_the_day_is_well_under` and
`test_google_quota_exceeded_surfaces_as_quota_exhausted_google`. `grep -rn --include=*.py
googleapiclient src/` finds it only in `youtube/transport.py`. `ruff check` and
`ruff format --check` are clean, and `ytscout --help` runs. No subcommand uses the client
yet, so there was no `--dry-run` to run; `collect --dry-run` still exits 2. No real API
calls were made.

Decisions, and why:

- **`tzdata` was added as a win32-only dependency.** Windows has no system IANA database,
  so `ZoneInfo("America/Los_Angeles")` fails there without it. Run
  `pip install -e ".[dev]"` again if an older venv is missing it.
- **The cost table (`UNIT_COSTS`) lives in `quota.py`, not `client.py`.** That way
  `DryRunTransport` can report units without a circular import. It is still one table, and
  the client reads every cost from it.
- **Keyword-only `etag` on `Transport.call`.** Beyond the issue's `call(resource, method, **params)`,
  `call` takes a keyword-only `etag`, which it sends as `If-None-Match`. A 304 raises
  `NotModified`, and `DataApi` then returns the cached body from `api_cache`. The charge
  still applies, and the `DataApi` docstring says so.
- **`Ledger(..., dry_run=True)`** checks both caps against the stored day total plus this
  run's would-be units, and writes nothing. `DataApi` skips the cache under a dry-run
  ledger, so empty dry-run bodies never poison it. **The CLI should pair `DryRunTransport`
  with a dry-run ledger.**
- **`charge()` commits its own write**, using `with conn:`. That means it also commits
  anything else pending on the same connection. A collector that needs atomic batches
  should checkpoint before calling the API, or use a separate connection for the ledger.
- The Google reasons `quotaExceeded` and `dailyLimitExceeded` both map to
  `QuotaExhausted(kind="google")`, with `used` and `cap` set to `None`. Other `HttpError`s,
  including `rateLimitExceeded`, pass through unchanged.
- SQL for `quota_ledger` and `api_cache` went into `store/repo.py`: `quota_used`,
  `add_quota_used`, `get_api_cache` and `put_api_cache`. That keeps to "only the store
  writes". `store/db.py` gained `utc_now()` (an aware datetime), which is still the only
  clock read. `Ledger` takes an optional `clock`; without one it reads `quota.utc_now` on
  each call, so tests can monkeypatch it.
- `search` always sends `part=snippet` and `type=video`. `channels` and `videos` send
  `maxResults=50` and refuse more than 50 ids, or none, before charging.
  `iter_playlist_items(playlist_id, max_pages=None)` follows `nextPageToken`.
- `FakeTransport`: a fixture can be a list of pages, keyed without `pageToken`. Each page
  uses its own `nextPageToken` if it has one; otherwise the fake invents `page1`,
  `page2`, …. Use `FakeTransport.key(resource, method, **params)` to build keys, because
  the params must match exactly what `DataApi` sends.

For 004: build the real ledger with `Ledger(conn, settings.quota.daily_cap, run_cap=args.max_units)`
and `GoogleTransport(settings.api_key)`, and map `QuotaExhausted` of any kind to exit 3.
