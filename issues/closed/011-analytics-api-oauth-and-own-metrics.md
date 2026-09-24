# 011 — YouTube Analytics API: `auth` + `collect --analytics` for the own channel

**Type**: AFK
**Blocked by**: 004
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/youtube/analytics.py, src/ytscout/youtube/oauth.py, src/ytscout/collect/analytics.py, src/ytscout/cli.py (auth), migration 0003, tests/test_analytics.py
**Milestone**: M1

## Why

Public data cannot show retention, CTR, traffic source or revenue. This pulls them for
Countdown Animal Kingdom and, through RPM, becomes the calibration point for every money
estimate the niche scout makes.

## Scope

- `youtube/oauth.py`: `load_credentials(token_path) -> Credentials | None` reads the
  token file (`Settings.token_path`, from `YT_TOKEN_PATH`), refreshes if expired (writes
  the refreshed token back), returns `None` if absent.
  `run_consent_flow(client_secret_path, token_path)` uses
  `google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file` with scopes
  `https://www.googleapis.com/auth/yt-analytics.readonly` and
  `https://www.googleapis.com/auth/yt-analytics-monetary.readonly`, `run_local_server(port=0)`,
  saves the token. **The session never runs the consent flow** (guard blocks `ytscout
  auth`); it is tested with a fake `Credentials` object.
- `check_scopes(credentials) -> list[str]` returns problems: the monetary scope missing,
  or any scope that is not `*.readonly` (write scopes such as `youtube.upload` have no
  place in a read-only tool). The token file currently in `scripts/.secrets/` was copied
  from the pipeline repo and fails both checks; issue 012 replaces it. `collect
  --analytics` refuses to run on a token with problems (exit 4, message naming
  `ytscout auth`).
- `ytscout auth` runs the consent flow; `ytscout auth --status` prints whether a token
  exists, whether it refreshes, and the scope verdict (scope names are fine to print; no
  token values). `--status` is allowed in sessions.
- `youtube/analytics.py`: `AnalyticsApi(credentials, transport)` with the same
  swappable-transport shape as 003 (`GoogleAnalyticsTransport` builds
  `youtubeAnalytics v2`; `FakeTransport` for tests). One method `query(**params)`.
  Analytics quota is separate from the Data API and not tracked by the ledger.
- `ytscout collect --analytics [--days 400] [--dry-run]`:
  1. Per-video totals over the window, in batches of ≤ 200 video ids via
     `filters=video==a,b,c`, `dimensions=video`, metrics `views, estimatedRevenue,
     estimatedMinutesWatched, averageViewDuration, averageViewPercentage, likes,
     subscribersGained, subscribersLost`. Try also `impressions,
     impressionsClickThroughRate`; if the API rejects them, retry without and store null
     (they are not available to most channels through this API — confirm and note).
  2. Channel-level per-day rows over the window: `dimensions=day`, metrics `views,
     estimatedRevenue, monetizedPlaybacks` → used for RPM by period.
  3. Channel-level traffic sources over the window: `dimensions=insightTrafficSourceType`.
  Write `own_analytics(video_id, window_start, window_end, …)` per video (hence
  `window_*` instead of `day` — the API gives per-video totals per window cheaply and
  per-video-per-day expensively) and new tables `own_daily(day, views, revenue_usd,
  monetized_playbacks)` and `own_traffic(window_start, window_end, source, views)`
  (migration 0003). `rpm_usd = estimatedRevenue / views × 1000` when views > 0.
- `--dry-run` prints the three queries it would run.
- Tests: fake transport returns fixture rows; assert `own_analytics` rows, `rpm_usd`
  arithmetic, the retry-without-impressions path, and that a missing token exits with a
  message naming `ytscout auth` (exit 4).

## Out of scope

- Running the consent flow or any real Analytics call (012).
- Using RPM in scoring (023).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_analytics.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout auth --status` prints `token: absent` (or
      present, refreshable or not, and the scope verdict) and exits 0, printing no token
      contents. Against the copied pipeline token it reports the missing monetary scope and
      the write scopes.
- [ ] `.venv\Scripts\python.exe -m ytscout collect --analytics --dry-run` prints the
      planned queries without a token present (dry run needs no credentials).
- [ ] `grep -rn "estimatedRevenue" src/` appears only in `youtube/analytics.py` and
      `collect/analytics.py` — revenue never reaches templates or logs by accident.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §4.5, §8.2`. Revenue and RPM stay in SQLite on this machine. Nothing in this
issue prints a money figure; the dashboard shows RPM only as a calibration badge later.
