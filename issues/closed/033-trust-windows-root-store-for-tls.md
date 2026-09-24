# 033 — Trust the Windows root store for Google API TLS

**Type**: AFK
**Blocked by**: none
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/youtube/transport.py, the Analytics transport (011), `ytscout doctor`, tests
**Milestone**: M0

## Why

The first real `collect --own` (issue 005) failed with
`ssl.SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED`. Avast Web/Mail Shield on this
PC intercepts HTTPS and re-signs it with "Avast Web/Mail Shield Root". That root is in the
Windows certificate store but not in the `certifi` bundle, which `httplib2` (under
`googleapiclient`) uses. Issue 005 got around it by pointing the `HTTPLIB2_CA_CERTS`
environment variable at a scratch bundle made of certifi plus
`C:\ProgramData\Avast Software\Avast\wscert.pem`. The weekly Task Scheduler job (013/014)
will not have that variable set, so it needs a permanent fix.

## Scope

- Make `GoogleTransport` (and the OAuth/Analytics transport when 011 lands) verify TLS
  against the Windows root store as well as certifi. Two candidate approaches:
  1. At startup, write a combined bundle to `data/ca-bundle.pem` (certifi plus the roots
     from `ssl.create_default_context().load_default_certs()` /
     `ssl.enum_certificates("ROOT")`), then pass `httplib2.Http(ca_certs=...)` through
     `build(..., http=...)`.
  2. The `truststore` package. Check whether it works with httplib2, which builds its own
     `SSLContext`.
  Pick one and record the choice in the Outcome.
- Respect an existing `HTTPLIB2_CA_CERTS` if it is set.
- `ytscout doctor` gains a line that says which CA source is in use. It makes no network
  call.
- May spend up to **10 real Data API units** for one `collect --own --max-units 10` run
  with `HTTPLIB2_CA_CERTS` unset, to prove the fix works.
- Never turn off TLS verification (`disable_ssl_certificate_validation`).

## Out of scope

- Changing Avast settings. OAuth consent (012).

## Acceptance criteria

- [ ] With `HTTPLIB2_CA_CERTS` unset, `collect --own --max-units 10` exits 0 on this PC.
- [ ] A unit test checks that the combined bundle holds certifi's roots plus at least one
      root from the Windows store (or checks the equivalent for `truststore`), with no
      network access.
- [ ] `ytscout doctor` reports the CA source.

## Notes

On a failed request the ledger charges the unit before the call is made, so the failed
run in 005 still cost 1 unit and left a `runs` row with status `error`. That is correct
behaviour and needs no change.

## Note from 012 (2026-09-24)

There are two TLS stacks, not one. The Data API and Analytics API clients go through
httplib2 (`HTTPLIB2_CA_CERTS`), but google-auth's token refresh and the consent flow go
through `requests` (`REQUESTS_CA_BUNDLE`). The fix must cover both, or pass a shared
`ca_certs`/`verify` into each. Issue 012 worked around it with `data/ca-bundle.pem`
(certifi + Avast's `wscert.pem`) and both env vars set; that file is gitignored and is a fine
permanent location for the combined bundle.

## Outcome (closed 2026-09-24)

**Choice: the combined bundle (approach 1), not `truststore`.** httplib2 builds its own
`SSLContext` from a PEM path (`load_verify_locations(ca_certs)`), so `truststore`'s context
injection would never reach the Data API or Analytics clients. A file that both stacks can
consume was the only option that covers httplib2 *and* `requests` with one mechanism.

- New module `src/ytscout/youtube/tls.py`. `ca_bundle(data_dir)` honours an existing
  `HTTPLIB2_CA_CERTS`, then `REQUESTS_CA_BUNDLE`; otherwise it writes
  `data/ca-bundle.pem` = certifi (121 roots) + every server-auth root from the Windows
  `ROOT` store via `ssl.enum_certificates` (46 on this PC, Avast's among them). The file is
  rewritten only when its content would change. Roots trusted for code signing only are
  skipped. Verification is never turned off; the tests assert it.
- `GoogleTransport(..., ca_certs=)` passes `httplib2.Http(ca_certs=...)` into `build()`.
  `GoogleAnalyticsTransport(..., ca_certs=)` wraps the credentials in
  `AuthorizedHttp(credentials, http=Http(ca_certs=...))`, which is what `build(credentials=)`
  does internally with a default `Http`; so token refresh inside the API client verifies
  against the bundle too.
- The `requests` side: `oauth.refresh_token`, `load_credentials`, `describe` and
  `run_consent_flow` take `ca_certs`; refresh uses `Request(session)` with
  `session.verify` set and the consent flow sets `flow.oauth2session.verify`.
- The CLI builds the bundle only when a request can follow: the Data API transport, the
  Analytics transport, a token refresh when a token file exists, and the consent flow. A
  run that stops for want of a token still creates no `data/`. A `TlsError` (an env var
  naming a missing file) prints `<command>: ...` and exits 1.
- `ytscout doctor` prints `tls ca bundle: <path> (certifi 121 + Windows root store 46)`,
  or `(N certificates, from HTTPLIB2_CA_CERTS)` when overridden, or `BROKEN - ...`. No
  network call.
- Proof: `collect --own --max-units 10` with both env vars unset exited 0 (3 units). The
  weekly Task Scheduler job needs no environment variables.
- `data/ca-bundle.pem` from 012 (certifi + `wscert.pem`) was replaced by the generated one;
  same path, same purpose, now maintained by the code.

Not covered: `youtube-transcript-api` uses its own `requests` session for youtube.com; it
is outside this issue's Google-API scope and has not been re-tested under Avast here.
