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
