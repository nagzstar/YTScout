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
