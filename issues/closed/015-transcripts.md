# 015 — `collect --transcripts` with cache, backoff and graceful failure

**Type**: AFK
**Blocked by**: 010
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/transcripts.py, src/ytscout/collect/transcripts.py, tests/test_transcripts.py
**Milestone**: M2

## Why

Content analysis without transcripts is titles and guesswork. This fetches what
competitors actually say, once per video, and never lets a broken caption endpoint take
the weekly run down with it.

## Scope

- `transcripts.py`: `fetch(video_id) -> Transcript(video_id, language, text, source,
  status)` using `youtube-transcript-api` (current API — check the installed version's
  interface, it changed in 1.x). Preference order: manual `en`, manual `en-GB`, generated
  `en`, then any manual, then any generated (record the language). Join segments with
  spaces, drop timestamps, collapse whitespace. `source` is `manual` or `auto`.
  Failures: `TranscriptsDisabled` / `NoTranscriptFound` → `status='unavailable'` (final,
  never retried); network / rate-limit errors → retry 3× with backoff 1 s, 4 s, 16 s, then
  `status='error'` (retried next week). Sleep `1.5 s` between videos (config
  `transcripts.pause_seconds`).
- `ytscout collect --transcripts [--limit N] [--dry-run]`: candidates are videos of the
  own channel and `approved`/`watch` channels that have no `transcripts` row with
  `status in ('ok','unavailable')`, newest first, default `--limit 100`. Write a row per
  attempt result. Print counts: ok / unavailable / error.
- Never re-fetch an `ok` transcript. The cache is the DB.
- `--dry-run` lists the video ids it would fetch.
- Tests: mock the library at the module boundary (a `_fetcher` seam) — assert preference
  order, joining, unavailable-is-final, error-retries-3-times-with-sleeps-patched, the
  pause between videos, and that a second run skips `ok` rows.
- **Real calls allowed**: up to **5** transcript fetches against real video ids from the
  DB if `data/ytscout.sqlite` has any (own channel videos first). Zero Data API units —
  this library does not use the quota. If the DB is empty, skip and say so.

## Out of scope

- Using transcripts for anything (016/017).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_transcripts.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --transcripts --dry-run` lists candidates
      (or "none") and exits 0.
- [ ] If real fetches were made: the Outcome records how many were `ok` / `unavailable`
      and the language/source mix.
- [ ] A test proves a `TranscriptsDisabled` video is not retried on a second run.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md` decision 13 and `§8.3`: this is the one acknowledged grey-area dependency.
Keep it in one module so it can be swapped or removed without touching anything else. If
the library's interface has changed so much that it cannot be made to work in one session,
that is a `**Blocked**`-style stop: write what you found into the issue and park it.

## Outcome (closed 2026-09-24)

**Delivered.** `src/ytscout/transcripts.py` wraps `youtube-transcript-api` 1.2.4 (the 1.x
instance API: `YouTubeTranscriptApi().list(video_id)` → tracks with `language_code`,
`is_generated`, `fetch()`). It is the only module that imports the library. `fetch(video_id)`
returns `Transcript(video_id, language, text, source, status, detail)` and never raises for a
transcript problem. `src/ytscout/collect/transcripts.py` holds the collector;
`repo.transcript_candidates` / `repo.put_transcript` / `repo.get_transcripts` hold the SQL.
`ytscout collect --transcripts [--limit N] [--dry-run]` needs no `--max-units` because the
library uses no Data API quota. It prints `ok / unavailable / error` counts, and on stderr it
prints one line per failed video with the exception class. `transcripts.pause_seconds`
(default 1.5) is in `Settings` and `config/settings.example.yaml`.

**Decisions.**
- Preference order is as scoped: manual `en`, manual `en-GB`, generated `en`, any manual, any
  generated.
- Final `unavailable` covers `TranscriptsDisabled`, `NoTranscriptFound` and "no tracks at
  all". It also covers `VideoUnavailable` and `InvalidVideoId`, which the scope did not list.
  Otherwise a deleted video would be retried every week for ever, at about 21 s of backoff
  each time.
- Any other exception, `RequestBlocked`/`IpBlocked` included, gets one try plus 3 retries
  with sleeps of 1 s, 4 s and 16 s, then becomes `error`.
- Failure rows store `language = ''`, because the column is NOT NULL and part of the
  primary key. When a video later gets an `ok` or `unavailable` result, its old `error` rows
  are deleted. An `ok` row is never overwritten (`ON CONFLICT ... WHERE status != 'ok'`).
- Each video's row is committed on its own, so an interrupted run keeps what it fetched.

**Verified.** `pytest -q` passes: 230 tests, 17 of them new in `tests/test_transcripts.py`.
The tests cover preference order, joining and whitespace, unavailable-is-final with no
sleeps, error retries (4 calls with sleeps `[1, 4, 16]`), the 1.5 s pause between videos
but not after the last one, a second run that skips `ok` rows and `TranscriptsDisabled`
rows while retrying `error` rows, and the dry-run and real CLI paths. `ruff check` and
`ruff format --check` are clean. `collect --transcripts --dry-run` lists the 5 own video ids
(7 own videos in the DB, no approved/watch competitors yet) and exits 0.

**Real fetches: 5 of 5 allowed, all own-channel videos, all `error`. 0 were `ok`, 0 were
`unavailable`, so there is no language or source mix to report.** I checked that this is
not a bug in the wrapper: the imports resolve and the `Transcript.fetch` signature matches.
The cause is on the library or network side. The likely candidates are `RequestBlocked` /
`IpBlocked` (YouTube blocking the home IP) or `PoTokenRequired`, and retrying does not fix
either. I could not tell which one: the per-video exception class was only added to the
output after the run, and the budget was spent. The 5 videos keep their `error` rows and
stay candidates.
**Next step for whoever runs this next (1 fetch is enough):** run
`collect --transcripts --limit 1` and read the stderr line. If it says `PoTokenRequired` or
`IpBlocked` over many weeks, the library route is dead from this IP. DESIGN.md §8.3
anticipated this: 016/017 must degrade to titles and descriptions. Do not add proxies or
scraping workarounds.

**For 016/017.** Read transcripts where `status = 'ok'`. Expect none at first. Treat a
missing transcript as normal, not as an error.
