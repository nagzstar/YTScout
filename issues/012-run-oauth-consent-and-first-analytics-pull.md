# 012 — Consent screen and the first real Analytics pull

**Type**: Active
**Blocked by**: 005, 011
**Add dirs**: none
**Covers**: scripts/.secrets/token.json (YT_TOKEN_PATH), data/ytscout.sqlite (own_analytics, own_daily, own_traffic)
**Milestone**: M1

## Why

One browser click, and the money model has a real number to calibrate against.

## Scope

1. If the OAuth consent screen in Google Cloud is in *Testing*, make sure your Google
   account is listed as a test user and both `yt-analytics` scopes are added. If you
   reused the pipeline's project (issue 005), its consent screen already exists; add
   `yt-analytics-monetary.readonly` to it.
2. The token at `YT_TOKEN_PATH` right now is a copy of the pipeline's: it carries
   `youtube.upload` and other write scopes and lacks the monetary scope, so it cannot
   read revenue and could publish to the channel. `ytscout auth` overwrites this repo's
   copy with a read-only token; the pipeline's own token file is untouched.
   ```powershell
   .venv\Scripts\python.exe -m ytscout auth
   ```
   A browser opens; sign in with the account that owns Countdown Animal Kingdom; accept
   both scopes. The token lands at `YT_TOKEN_PATH` (`scripts/.secrets/token.json`).
3. ```powershell
   .venv\Scripts\python.exe -m ytscout auth --status
   .venv\Scripts\python.exe -m ytscout collect --analytics --dry-run
   .venv\Scripts\python.exe -m ytscout collect --analytics
   ```
4. Check which columns came back: `sqlite3 data\ytscout.sqlite "select count(*), count(impressions),
   count(ctr) from own_analytics"`.

## Out of scope

- Anything with the numbers. They stay in the DB.

## Acceptance criteria

- [ ] `auth --status` reports the token present, refreshable, with both analytics scopes
      and no write scopes.
- [ ] `own_analytics` has one row per video for the window; `own_daily` has ~400 rows;
      `own_traffic` has ≥ 3 sources.
- [ ] The Outcome says which metrics were null (impressions/CTR especially) — **not** any
      revenue or RPM figure. "RPM populated for N videos" is enough.

## Notes

If Google says the app is unverified, that is expected for a personal Testing-mode app:
click through Advanced → continue. If the token expires after 7 days (Testing mode does
that), note it; issue 030's `doctor` will surface it, and the fix is moving the consent
screen to Production, which needs no verification for these scopes on your own channel.
