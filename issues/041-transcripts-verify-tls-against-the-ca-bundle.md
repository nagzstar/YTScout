# 041 — Transcripts must verify TLS against the 033 CA bundle

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/transcripts.py, src/ytscout/collect/transcripts.py, src/ytscout/cli.py (`_collect_transcripts`), tests/test_transcripts.py
**Milestone**: M5

## Why

The first end-to-end weekly run (018, 2026-09-25 13:54) fetched 0 transcripts: all 100
candidates ended `error`. A one-off `transcripts.fetch(video_id, backoff=())` shows the
exception is `requests.exceptions.SSLError`: `CERTIFICATE_VERIFY_FAILED: unable to get
local issuer certificate` for `www.youtube.com`. Issue 033 wired `data/ca-bundle.pem`
(certifi + the Windows root store, so Avast's interception root is trusted) into httplib2
and the OAuth `requests` calls, but `youtube-transcript-api` builds its own
`requests.Session()` and still verifies against certifi alone. Every video then costs the
full 1 + 4 + 16 s backoff, so the step burns 37 minutes a week for nothing, and 039's
circuit breaker never trips because the failure is not `RequestBlocked`.

## Scope

- `transcripts._library_fetcher` (or a module-level `configure(ca_certs: Path | None)`)
  passes `YouTubeTranscriptApi(http_client=session)` where `session = requests.Session()`
  and `session.verify = str(ca_certs)`. Verification is never turned off.
- `cli._collect_transcripts` obtains the bundle path the same way the Data API transport
  does (`youtube.tls.ca_bundle(data_dir)`, honouring `HTTPLIB2_CA_CERTS` /
  `REQUESTS_CA_BUNDLE`) and hands it to the fetcher before collecting. `--dry-run` builds
  nothing.
- A systemic failure must not cost 21 s per video: after `BLOCK_STREAK_LIMIT` consecutive
  `error` results whose detail is `SSLError` or `ConnectionError`, the collector stops
  exactly like the 039 breaker, reports `stopped after N consecutive <detail>`, and leaves
  the rest untouched. Widen `TranscriptCounts.stopped_after` and the CLI line accordingly.
- Real network calls: none. Prove the session wiring by asserting on the `http_client`
  object handed to a fake `YouTubeTranscriptApi`, and the breaker with a fake fetcher that
  raises `SSLError`.

## Out of scope

- Proxies, cookies or any other way round an IP block.
- Retuning the backoff for genuine transient errors.

## Acceptance criteria

- [ ] A test proves the `requests.Session` given to the library has `verify` set to the
      bundle path returned by `youtube.tls.ca_bundle`.
- [ ] A test proves 5 consecutive `SSLError` results stop the run with
      `stopped_after == 5` and the remaining candidates untouched, and that a mixed run
      (`error`, `ok`, `error`) does not stop.
- [ ] `ytscout collect --transcripts --dry-run` still lists candidates and writes no
      `data/ca-bundle.pem`.
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .` clean.

## Notes

`issues/closed/033-*.md` Outcome for the bundle mechanics; `issues/closed/039-*.md` for the
breaker. `DESIGN.md §8.3`. The 01:04 run the same day *did* fetch 32 transcripts before
being IP-blocked, so the verification failure may be intermittent (Avast interception
switching on); the fix must work in both states, which passing the bundle does.
