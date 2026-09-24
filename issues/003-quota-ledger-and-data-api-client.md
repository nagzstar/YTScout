# 003 — Quota ledger and YouTube Data API client with swappable transport

**Type**: AFK
**Blocked by**: 002
**Add dirs**: none
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
