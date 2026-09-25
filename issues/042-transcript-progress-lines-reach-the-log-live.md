# 042 — Transcript results reach the weekly log as they happen

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-haiku-4-5-20251001 medium
**Covers**: src/ytscout/collect/transcripts.py, src/ytscout/cli.py (`_collect_transcripts`), tests
**Milestone**: M5

## Why

Issue 038 made `run_weekly.ps1` run Python unbuffered so per-video lines land in the log
live. `collect --transcripts` defeats that: `collect_transcripts` accumulates
`counts.failures` and the CLI prints them all after the loop. During the 018 run the log
showed `RUN python -u -m ytscout collect --transcripts` and then nothing for 37 minutes;
the only way to see progress was to query the `transcripts` table. A reader cannot tell a
slow step from a hung one.

## Scope

- `collect_transcripts` takes an optional `on_result: Callable[[str, str, str | None], None]`
  (video id, status, detail) called after each row is committed. The CLI passes a printer
  that writes `  <video_id>: <status> (<detail>)` to stderr immediately (`ok` lines too,
  so a healthy run is visible). The end-of-run summary line stays.
- Keep `counts.failures` for tests and for the summary; do not print the failures twice.

## Out of scope

- Any change to what is fetched or when.

## Acceptance criteria

- [ ] A test with a fake fetcher and a recording callback proves one call per candidate,
      in order, before the function returns.
- [ ] `ytscout collect --transcripts` against a fake fetcher (existing CLI test pattern)
      prints one line per video and one summary line.
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .` clean.

## Notes

`issues/closed/038-*.md` for the unbuffered logging; the 018 log
`logs/weekly-20260925-1354.log` for what the gap looks like.
