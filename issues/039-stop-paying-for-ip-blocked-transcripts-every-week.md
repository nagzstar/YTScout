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
