# 039 — Stop paying 21 seconds a video for IP-blocked transcripts every week

**Type**: AFK
**Blocked by**: 014
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/transcripts.py, src/ytscout/collect/transcripts.py, src/ytscout/store/, tests/test_transcripts.py
**Milestone**: M5

## Why

The first weekly run (014) fetched 100 transcript candidates: 32 `ok`, 1 `unavailable`,
67 `error`, every error `IpBlocked`. Each `IpBlocked` costs one try plus three retries
with 1 s, 4 s and 16 s sleeps, so the step took 30 minutes, 23 of them sleeping on a
block that retrying does not lift. Every `error` row stays a candidate, so next week the
same 67 videos cost the same 23 minutes again, and the new ones behind them never get
their turn inside the 100-per-run cap.

## Scope

- **Circuit breaker inside a run.** After 5 consecutive `IpBlocked`/`RequestBlocked`
  results, stop fetching for this run, print
  `collect --transcripts: stopped after N consecutive IpBlocked; M candidates untouched`,
  and exit 0. The untouched candidates keep whatever status they had.
- **No retries for a block.** `IpBlocked`/`RequestBlocked` get one attempt, no backoff.
  Other exceptions keep the 1/4/16 s retries.
- **Blocked rows wait a week, not a run.** Add a `blocked` status (migration). A `blocked`
  row is a candidate again only when `fetched_at` is older than 7 days. `error` rows keep
  today's behaviour. `ok` rows are never refetched.
- **Fresh videos first.** `transcript_candidates` orders by published date descending, so
  the newest videos get the run's budget before old failures are retried.
- Tests for all four rules against the fake transport; the two-run test in
  `tests/test_transcripts.py` is extended for the 7-day rule.
- No proxies, no cookies, no alternative transcript sources, no real fetches (DESIGN.md
  §8.3: degrade to titles and descriptions; 015 Outcome).

## Out of scope

- Finding out why the IP is blocked or whether it lifts.

## Acceptance criteria

- [ ] With a fake that raises `IpBlocked` for every id, `collect --transcripts` on 100
      candidates makes exactly 5 fetch calls, sleeps 0 s, prints the stopped line, exits 0.
- [ ] A `blocked` row fetched 3 days ago is not a candidate; one fetched 8 days ago is.
- [ ] `collect --transcripts --dry-run` lists candidates newest first.
- [ ] `pytest -q` passes; `ruff check` clean.

## Notes

The blocked ids and the timings are in `logs/weekly-20260925-0104.log` and the 014
Outcome. The library is `youtube-transcript-api` 1.2.4; its exception classes are
`youtube_transcript_api._errors.IpBlocked` and `RequestBlocked`.

## Outcome (closed 2026-09-25)

Delivered all four rules, tested against the fake `_fetcher`; no real fetches made.

- **No retries for a block.** `transcripts.fetch` returns status `blocked` (detail = the
  exception class) after one attempt for `RequestBlocked` and its subclass `IpBlocked`. No
  backoff sleeps. Every other non-final exception still gets the 1/4/16 s retries.
- **Circuit breaker.** `collect_transcripts` stops after `BLOCK_STREAK_LIMIT = 5`
  consecutive `blocked` results. `TranscriptCounts` records `stopped_after` and `untouched`,
  and the CLI prints `collect --transcripts: stopped after 5 consecutive IpBlocked; 95
  candidates untouched`, then exits 0. Untouched candidates get no row. Any other result
  resets the streak. If the streak reaches 5 on the last candidate, nothing is left to stop,
  so no line is printed. Decision: no inter-video pause follows a `blocked` result. Without
  that, the "sleeps 0 s" criterion could not hold with the configured pause. A refused
  request is not load that needs spacing out.
- **A week off.** Migration `0011` rebuilds `transcripts` with a CHECK on
  `ok|unavailable|blocked|error`. The old table had no constraint, so the rebuild is what
  makes the new status explicit. `transcript_candidates` skips a video that has a `blocked`
  row with `fetched_at` younger than 7 days (`repo.BLOCKED_RETRY_AFTER`). `error` rows stay
  candidates on every run. An `ok` or `unavailable` result deletes `error` and `blocked`
  rows. `packets.transcript_for_packet` ranks `blocked` between `unavailable` and `error`.
- **Fresh first.** The candidate order was already `published_at DESC`. It is now proved by
  a CLI dry-run test and checked against the real DB (read-only): the top 5 run from
  2026-09-15 down to 2026-09-14.
- The CLI summary line now ends `, blocked N`.
- DESIGN.md §8.3 has a line on the block rule.

Acceptance: the 100-candidate all-`IpBlocked` CLI test asserts 5 fetch calls, `sleeps ==
[]`, the stopped line and exit 0. The 3-day vs 8-day test is
`test_blocked_rows_wait_a_week_and_errors_retry_every_run`. The dry-run order is covered in
`test_cli_dry_run_lists_ids_and_fetches_nothing`. `pytest -q` passes (540); `ruff check`
is clean.

For the next run: the 67 IpBlocked videos from 014 are stored as `error`, because the
exception class was never stored. The next weekly run will retry them, oldest last. Once 5
of them in a row come back blocked (or 5 new ones do), the breaker stops the run and they
become `blocked`. Whether the IP block lifts is still unknown and out of scope.
