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

## Outcome (closed 2026-09-24)

Delivered: `youtube/oauth.py`, `youtube/analytics.py`, `collect/analytics.py`, migration
`0003_own_analytics.sql`, a real `ytscout auth [--status]` in place of the stub, and
`collect --analytics [--days 400] [--dry-run]`. `tests/test_analytics.py` has 21 tests;
206 pass in total.

- **The OAuth API.** `load_credentials(token_path, *, refresh=True)` returns `None` when
  the file is absent. An expired token with a refresh token is refreshed and written
  back. If the file won't parse or the refresh fails, it raises `TokenError`, and that
  covers network failures too. The error message never contains token contents.
  `refresh_token()` is split out so that `describe()` can report scopes even when a
  refresh fails. `check_scopes` reads `granted_scopes` or, failing that, `scopes`. It
  flags a missing `yt-analytics-monetary.readonly` scope and any scope that does not end
  in `.readonly`.
- **`auth --status` always exits 0.** It prints `token: absent|present`, expired,
  whether there is a refresh token, the refresh result, the scope names and the verdict.
  Run against the copied pipeline token it printed: missing monetary scope; not
  read-only: `youtube`, `youtube.force-ssl`, `youtube.upload`. **Issue 012 has to replace
  this token with `ytscout auth`.**
- **Not verified: the token refresh.** In this session the refresh failed with
  `SSL CERTIFICATE_VERIFY_FAILED` against `oauth2.googleapis.com`. That looks like the
  sandbox's network. `--status` reports it as `refresh: failed - ... TransportError`.
  Check it on the real machine in 012.
- **Deviation: `AnalyticsApi(transport)`, not `(credentials, transport)`.** This mirrors
  `DataApi`/`GoogleTransport`: the credentials go to `GoogleAnalyticsTransport(credentials)`.
  `query(**params)` defaults to `ids=channel==MINE` and returns the rows as dicts keyed
  by column header. Tests can swap in `FakeAnalyticsTransport(handler)`, and
  `DryRunAnalyticsTransport` backs the dry run. The CLI seams are
  `cli.make_analytics_transport(credentials, dry_run)` and `cli.load_credentials`.
- **`collect --analytics` does not need `--max-units`.** Analytics quota is not part of
  the Data API ledger. The window is `--days` long and ends yesterday, inclusive.
  - Video ids come from the own channel's `videos` rows, so `collect --own` has to run
    first. With no videos, only the channel-level rows are collected, and a note says so.
  - Queries go in batches of 200 with `sort=-views` and `maxResults=200`, which the docs
    require for video-dimension reports.
  - Exit 4, naming `ytscout auth`, when the token is absent, won't load or refresh, or
    has scope problems.
  - A `runs` row is written with kind `collect_analytics`.
  - The summary prints counts only. No money figure is printed.
- **Impressions and CTR.** The Analytics API metrics docs
  (developers.google.com/youtube/analytics/metrics, read 2026-09-24) do not list
  `impressions` or `impressionsClickThroughRate` as metrics. `adImpressions` is noted
  there as "formerly named impressions". So the first video batch asks for both, and a
  `QueryRejected` (HTTP 400) retries that batch without them. Later batches don't ask,
  and the columns are stored as NULL. **Risk for 012:** if the API accepts `impressions`
  as the old alias for ad impressions, the `impressions` column would hold ad
  impressions. Check what the real response contains, and drop the metric if that
  happens.
- **Schema.** `own_analytics` gains `minutes_watched`, `likes` and `collected_at`.
  `traffic_json` stays NULL, because per-video traffic would cost a query per video.
  Two new tables: `own_daily(day PK, views, revenue_usd, monetized_playbacks,
  collected_at)` and `own_traffic(window_start, window_end, source, views,
  collected_at)`. All three tables are upserted on their key: YouTube revises recent
  revenue, and history is kept by window/day. They are not append-only snapshot tables.
  `monetized_playbacks` in `own_analytics` stays NULL, since the per-video metric list in
  the Scope does not include it.
- **The grep criterion.** `grep -rn estimatedRevenue src/` matches only
  `youtube/analytics.py` and `collect/analytics.py`, plus their gitignored `.pyc` files.
- **Not exercised: the consent flow (`ytscout auth` without `--status`).** It is blocked
  in sessions and is the job of 012.
