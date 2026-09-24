# YT Scout — Project Design Document

**Status:** Draft v1 — 24 Sep 2026
**Owner:** Nagz
**Audience:** Whoever (human or Claude Code) builds this repo. Read this first.

---

## 1. What this is

A personal, local tool that answers two questions:

1. **Niche Scout** — *Which YouTube niches are worth entering, ranked by estimated £ earned per hour of manual work, given that most of the production is done by an AI pipeline?*
2. **Competitor Analyser** — *For my existing channel (Countdown Animal Kingdom, top-5 animal Shorts), who are my real competitors, what are they doing better or worse than me, and where are the gaps?*

The ranking rule for niches is fixed and simple:

> **Score = estimated £ / month ÷ manual hours / month.**
> Money beats time put in manually. A niche that pays £300/month for 2 hours of human work beats one that pays £900/month for 20 hours.

Everything else in this document exists to make that number honest.

---

## 2. Decisions already made

These were settled in the design conversation. Do not reopen them without a reason; do note the reason if you do.

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Who is it for? | **Just Nagz.** No auth, no billing, no multi-tenant. | Scope control. Can be opened up later; not designed for it now. |
| 2 | "Money > time" means | **A ranking rule for niches**, not a goal for the app itself. | The app can take effort to build; the niches it recommends must not. |
| 3 | Data source | **Official YouTube Data API v3** (free tier, 10k units/day). **No scraping of YouTube pages.** One Cloud project only. | Nagz owns a monetised channel; ToS risk is real. Multiple projects to multiply quota breaks the API terms. Quota cannot be bought — only requested via Google's audit form, which is slow and can be denied. |
| 4 | Formats | **Shorts and long-form scored separately.** Every niche gets two scores. | Money model and manual effort differ by an order of magnitude between formats. |
| 5 | What is a "niche"? | **Format × topic** (e.g. `top5-countdown × dangerous-animals`, `explainer-longform × personal-finance-uk`). | A talking-head finance video and a faceless finance Short are different businesses. Topic-only scoring blurs them. |
| 6 | Opportunity score | **Small-channel outlier rate** is the core signal. Total niche views is secondary. | For a newcomer, what matters is whether channels under ~10k subs / under a year old are pulling outlier views. That means demand outstrips supply. Giant-dominated niches are bad bets regardless of RPM. |
| 7 | Manual-hours denominator | **Audit the existing pipeline repo** (`top-five-animals-1`) and score niches by how many production steps it already covers. | Turns "AI-able" from a vibe into a measured fraction. |
| 8 | Competitor analysis depth | **Metrics + LLM content analysis** (titles, descriptions, transcripts). | Numbers say *what*; content analysis says *why* and *what's missing*. |
| 9 | Own-channel data | **Yes — connect YouTube Analytics API via OAuth** for Countdown Animal Kingdom. | Unlocks retention, CTR, traffic sources and real RPM per video. Also the calibration point for the money model. |
| 10 | Money model | **AdSense only.** `£ = est. views × RPM tier`, tiers editable, calibrated to real Shorts RPM. | Deterministic and comparable. Affiliate/sponsor potential is fuzzy; leave it out of v1. |
| 11 | Where it runs | **Local**: Python on Nagz's Windows PC, SQLite, static HTML dashboard, Windows Task Scheduler. | Free, no infra, keys stay local. Hosted (Supabase/Cloudflare) can come later if always-on matters. |
| 12 | Build order | **Competitor Analyser first**, then Niche Scout. | Immediately useful, smaller, and it builds the shared plumbing (channel → uploads → video stats → transcripts) the scout needs anyway. |
| 13 | Transcripts | **`youtube-transcript-api`** (Python library). Acknowledged grey area: read-only, widely used, not an official API. | The official `captions.download` only works for videos you own. Content analysis without transcripts is thin. Degrade gracefully to titles + descriptions if it breaks. |
| 14 | LLM | **Claude, via Nagz's Claude subscription — no Anthropic API key.** Implemented by calling Claude Code non-interactively (`claude -p`). | Nagz's constraint. Verified against Claude Code docs (see §7). |
| 15 | Competitor selection | **Auto-discover via API search around the niche; Nagz approves/rejects** in the dashboard. | Catches competitors he doesn't know about. Quota cost is bounded. |
| 16 | Cadence | **Weekly refresh, 12-month rolling history.** | Cheap on quota, enough to see trend direction for niches and competitors. |

---

## 3. Non-goals (v1)

- Not a SaaS. No users other than Nagz.
- No scraping of YouTube HTML, no headless browsers against YouTube, no third-party scraper APIs.
- No affiliate / sponsorship / merch revenue modelling.
- No video generation. This tool *decides*; the existing pipeline *produces*.
- No mobile app. The dashboard is a local HTML file.
- Not a real-time tool. Weekly is the heartbeat.

---

## 4. Module A — Competitor Analyser

### 4.1 Purpose
Given Nagz's channel, find the channels actually competing for the same audience, compare them on public metrics, and use Claude to explain the differences and surface gaps.

### 4.2 Flow

```
[Own channel (OAuth)] ──┐
                        ├──> collect ──> SQLite ──> analysis packet (JSON)
[Competitors (public)] ─┘                              │
                                                       ▼
                                             claude -p (subscription)
                                                       │
                                                       ▼
                                          structured findings (JSON) ──> SQLite ──> dashboard
```

### 4.3 Competitor discovery
1. Seed queries are derived from the own channel's recent titles and tags (e.g. "top 5 deadliest animals", "most dangerous animals shorts").
2. `search.list` (type=video, order=viewCount and order=date, publishedAfter = 12 months) → candidate channel IDs. Budget: **≤ 20 searches per discovery run** (2,000 units).
3. `channels.list` on candidates (1 unit each) → subs, video count, country, upload playlist.
4. Similarity filter: same format (Shorts share ≥ 70% of recent uploads), overlapping topic keywords, size band within 0.1×–10× of own channel.
5. Present candidates in the dashboard with **approve / reject / watch**. Only approved channels are tracked weekly. Rejected IDs are remembered so they aren't re-suggested.

### 4.4 Metrics (per channel, per format, rolling 90 days and 12 months)
- Uploads per week
- Median views per video; p25/p75; max
- Views-per-subscriber ratio
- Velocity: views at 24h / 7d / 30d for videos captured early enough (from weekly snapshots)
- Length distribution
- Title patterns: length, numerals, capitalisation, question marks, "top N" phrasing
- Outlier count (videos ≥ 3× the channel's own median)
- Share of niche total views

### 4.5 Own-channel extras (YouTube Analytics API, OAuth)
- Views, estimated revenue, RPM, monetised playbacks per video
- Average view duration and average % viewed (retention)
- Impressions and CTR where the Analytics API exposes them for the channel
- Traffic sources (Shorts feed vs search vs suggested)
- Subscriber delta per video

These never leave the machine. They are stored in a separate table and are the calibration source for the money model (§6.3).

### 4.6 Content analysis (Claude)
Inputs per run: for each approved competitor and for the own channel, the last N videos (default 15) with title, description, tags, length, view stats, and transcript (if available).

Claude is asked, with a fixed JSON schema, for:
- **Per competitor:** what they consistently do (hook style, structure, pacing cues from the transcript, title formula, topic clusters); what they do that we don't; what we do that they don't.
- **Topic gap list:** topics/angles covered by ≥ 2 competitors with above-median views that Countdown Animal Kingdom hasn't covered.
- **Our weaknesses:** patterns in our below-median videos vs our above-median ones.
- **Five concrete next-video suggestions**, each tied to evidence (video IDs).

Large inputs are summarised per video first (short call), then compared (one call). This keeps each `claude -p` call well inside the 10 MB stdin cap and keeps subscription usage modest.

---

## 5. Module B — Niche Scout

### 5.1 Purpose
Generate candidate niches, validate them with real data, and rank them by £ per manual hour, separately for Shorts and long-form.

### 5.2 Candidate generation
Two feeds, merged and de-duplicated:

1. **LLM brainstorm** — Claude proposes candidate `format × topic` niches from a prompt that includes: the pipeline's capabilities (§6.4), constraints (faceless, English-language, evergreen preferred), and a list of niches already scored (so it explores rather than repeats). Output is a fixed schema: `{format, topic, example_search_queries[3], why_ai_able, suspected_manual_steps[]}`.
2. **Snowball** — from every approved competitor and every channel found in a validated niche, follow `search.list` on their top titles and `channels.list` on co-occurring channels to find adjacent niches.

Nagz can add seeds by hand in `config/seed_niches.yaml`.

### 5.3 Validation (per niche, per format)
1. Run the niche's 3 search queries (`search.list`, 100 units each, publishedAfter = 12 months, order = viewCount and order = date). **≤ 6 searches per niche** (600 units).
2. Collect the distinct channels (target: 30–80).
3. `channels.list` on all (1 unit each). Classify **small** = `subs < 10,000 AND channel age < 12 months`.
4. For each channel, `playlistItems.list` on the uploads playlist (1 unit/page, 50 per page) → last 30 video IDs, then `videos.list` in batches of 50 (1 unit/batch) → stats, duration (Shorts ≤ 60s… treat ≤ 3 min as Shorts-eligible per current YouTube rules, but classify by the channel's own format mix), publish date.
5. Compute the metrics in §6.

Typical cost: **~0.9–1.2k units per niche**. A discovery week validating 6–8 new niches plus refreshing existing ones fits in 10k/day. See §8 for the budget.

### 5.4 Niche lifecycle
`proposed → validated → scored → (tracking | shelved)`. `scout validate` moves a niche to `validated`; `score` moves it to `scored`; Nagz picks `tracking` or `shelved` in the dashboard. Shelved niches keep their data and can be re-scored. Tracking niches are refreshed weekly at a fraction of the validation cost (no new searches; just `videos.list` on known channels' new uploads).

---

## 6. Scoring model

All thresholds live in `config/scoring.yaml` and are tunable. Defaults below.

### 6.1 Opportunity score (0–1)
```
small_channels          = channels in niche with subs < 10k AND age < 12mo
outlier(video)          = views ≥ 3 × median(views of that channel's last 30 videos)
                          AND views ≥ 10,000 (Shorts) / 2,000 (long-form)
small_outlier_rate      = |{small channels with ≥1 outlier in last 90d}| / |small_channels|
newcomer_view_share     = Σ views(small channels, 90d) / Σ views(all channels in niche, 90d)
concentration           = share of 90d views held by the top 3 channels   (lower is better)

opportunity = 0.5 × small_outlier_rate
            + 0.3 × newcomer_view_share
            + 0.2 × (1 − concentration)
```
If `|small_channels| < 5`, opportunity is flagged **low-confidence** and capped at 0.4.

### 6.2 Expected views for a newcomer
```
newcomer_monthly_views = median(monthly views of small channels in niche, last 90d)
```
Report p25 and p75 alongside. This is deliberately conservative: it estimates what a typical *small* channel gets, not the niche's best case.

### 6.3 Money (£/month)
```
rpm_gbp                 = rpm_table[niche.topic_category][format] × calibration
calibration             = own_actual_rpm / rpm_table["animals"]["shorts"]     (from Analytics API)
est_monthly_gbp         = newcomer_monthly_views / 1000 × rpm_gbp
```
- `config/rpm_tiers.yaml` holds the table. Seed it with public creator-reported ranges (finance/business high, tech/education mid, entertainment/animals low; Shorts a small fraction of long-form). Every row carries a `source` and `last_reviewed` field. Values are in USD and converted with a fixed `usd_gbp` rate in config.
- Calibration is recomputed each run from the own channel's actual RPM. If the Analytics API isn't connected, calibration = 1.0 and the dashboard shows an "uncalibrated" badge.
- The topic category for a niche is assigned by Claude during brainstorm/validation from a fixed list of categories that match the RPM table's rows.

### 6.4 Manual hours / month
Production is modelled as a fixed list of steps. The **pipeline audit** (Milestone 3) fills in which steps `top-five-animals-1` automates today.

| Step | Default hours/video if manual | Notes |
|------|------------------------------|-------|
| Topic research & fact-check | 0.75 | |
| Script | 0.5 | |
| Voiceover | 0.25 | TTS makes this ~0 |
| Visual sourcing (stock/AI images/clips) | 1.0 | Niches needing *specific real footage* set this to 2.5+ |
| Screen recording / gameplay / original footage | 3.0 | Only applies to niches that need it; usually disqualifying |
| Face-cam / presenter | 4.0 | Disqualifying for a faceless pipeline |
| Assembly & edit | 1.0 | ffmpeg/moviepy makes this ~0 |
| Thumbnail | 0.25 | Shorts: ~0 |
| Title / description / tags | 0.15 | |
| Upload & schedule | 0.1 | API upload makes this ~0 |
| QA / review before publish | 0.15 | Floor: never below 0.1 — a human should look |
| Community / comments | 0.1 | |

For each niche, Claude tags which steps are **required** (e.g. `needs_original_footage: true`). Then:
```
manual_hours_per_video = Σ hours(step) for steps required by the niche AND not automated by the pipeline
videos_per_month       = config default (Shorts: 20, long-form: 4), overridable per niche
manual_hours_per_month = manual_hours_per_video × videos_per_month
```

### 6.5 Final score
```
score_gbp_per_manual_hour = est_monthly_gbp / max(manual_hours_per_month, 2)
```
The floor of 2 hours/month stops fully-automated niches from producing infinite scores.

The dashboard shows, per niche per format: **score**, opportunity, est £/month (with p25–p75 band), manual h/month, confidence flags, 12-month trend arrow. Rank by score; filter by opportunity ≥ threshold.

---

## 7. LLM layer — Claude via subscription, no API key

**Constraint:** Nagz will not use an Anthropic API key. All LLM calls go through his Claude subscription.

**Mechanism (verified against Claude Code docs, Sep 2026):**
- Claude Code's non-interactive mode `claude -p "<prompt>"` runs a one-shot session and exits. Without `--bare`, it uses the same subscription login as an interactive session. **Do not pass `--bare`** — bare mode skips OAuth credentials and requires an API key.
- `--output-format json --json-schema '<schema>'` returns a `structured_output` field conforming to the schema. Every analysis call in this project uses a schema from `schemas/`.
- `--allowedTools "Read"` lets Claude read the packet file without prompting. `--permission-prompts none` (Claude Code ≥ v2.1.259) makes unattended runs deny-and-continue rather than hang.
- Piped stdin is capped at 10 MB; pass a **file path** in the prompt instead of piping large packets.
- Run from the repo root so the repo's `CLAUDE.md` loads and gives Claude project context.

**Example invocation (PowerShell, from repo root):**
```powershell
$prompt = Get-Content prompts/competitor_analysis.md -Raw
$schema = Get-Content schemas/competitor_analysis.json -Raw
claude -p "$prompt`n`nPacket: data/packets/2026-09-28-competitors.json" `
  --output-format json --json-schema $schema `
  --allowedTools "Read" --permission-prompts none `
  | python -m ytscout.ingest_analysis --kind competitor
```

**Prompts are code.** They live in `prompts/`, are versioned, and every stored analysis records the prompt file hash it was produced with.

**Usage discipline.** Subscription usage is finite. The weekly run should stay small: summarise per video with a compact schema, then one comparison call per module. Never send raw transcripts of more than ~40 videos in a single call.

**Fallback.** If `claude` is not on PATH or not logged in, the run stores metrics only, marks the analysis as `pending`, and the dashboard shows it. Re-running `ytscout analyse` picks it up.

**Alternative considered:** Claude Code Desktop scheduled tasks can also run on the subscription and would remove the Task Scheduler dependency. Kept as an option; not the default because Task Scheduler is simpler to reason about and the collector is plain Python.

---

## 8. Data sources and quota budget

### 8.1 YouTube Data API v3 (public data)
Unit costs: `search.list` = **100**; `channels.list`, `playlistItems.list`, `videos.list` = **1** (videos.list takes up to 50 IDs per call).

| Weekly job | Calls | Units |
|------------|-------|-------|
| Competitor discovery (monthly, not weekly) | 20 searches | 2,000 |
| Refresh ~25 approved competitors: uploads + stats | 25 × 2 + 25 | ~75 |
| Validate 6 new niches | 6 × ~1,000 | ~6,000 |
| Refresh ~30 tracked niches (known channels only) | 30 × ~40 | ~1,200 |
| Own channel uploads/stats | ~5 | 5 |
| **Discovery week total** | | **~9,300** |
| **Ordinary week total** | | **~1,300** |

Rules:
- The collector keeps a local ledger of units used today and **stops at 9,000**, resuming the next day (quota resets midnight Pacific = 08:00 UK).
- Cache everything with ETags; re-fetch video stats only for videos < 90 days old (older ones move slowly).
- Never `search.list` for something a playlist or channel call can answer.
- One Google Cloud project. No workarounds.

### 8.2 YouTube Analytics API (own channel only)
- OAuth 2.0, scopes: `yt-analytics.readonly`, `yt-analytics-monetary.readonly`.
- Separate quota from the Data API; a weekly per-video report is negligible.
- All secrets live in `.env` at the repo root (gitignored), the same convention as the production pipeline repo: `YT_API_KEY` (Data API key), `YT_CLIENT_SECRET_PATH` (OAuth desktop client JSON, default `scripts/.secrets/client_secret.json`), `YT_TOKEN_PATH` (the granted token, default `scripts/.secrets/token.json`), `YT_CHANNEL_ID`. `scripts/.secrets/` is gitignored. Code reaches them only through `ytscout.settings`; nothing prints them.
- The OAuth client may be the pipeline's (one Cloud project shared by both tools) or a separate `ytscout` project; issue 005 records the choice. If shared, the pipeline's uploads (1,600 units each) draw on the same 10,000/day and the ledger cannot see them, so a Google `quotaExceeded` is handled like ledger exhaustion (exit 3).

### 8.3 Transcripts
- `youtube-transcript-api` for competitor videos (auto-generated captions are fine).
- Cache to SQLite; never re-fetch a transcript.
- Expect breakage. Wrap in retries with backoff; on persistent failure, mark `transcript_status = unavailable` and proceed with titles/descriptions.

### 8.4 Explicitly not used
- HTML scraping of youtube.com, Social Blade, vidIQ, etc.
- Google Trends (no official API worth depending on; revisit if one appears).
- Any third-party "YouTube data" API that itself scrapes.

---

## 9. Architecture

```
┌───────────────────────────── Nagz's Windows PC ─────────────────────────────┐
│                                                                              │
│  Windows Task Scheduler (weekly, Mon 03:00)                                  │
│      └─ run_weekly.ps1                                                       │
│           ├─ python -m ytscout collect     ── YouTube Data API ──────────┐   │
│           │                                ── YouTube Analytics (OAuth) ─┤   │
│           │                                ── youtube-transcript-api ────┘   │
│           │            ▼                                                     │
│           │        data/ytscout.sqlite  ◄──────────────────────────────┐    │
│           │            │                                                │    │
│           ├─ python -m ytscout packet      → data/packets/*.json        │    │
│           ├─ claude -p … (subscription)    → structured JSON ───────────┘    │
│           ├─ python -m ytscout score                                         │
│           └─ python -m ytscout dashboard   → dashboard/index.html            │
│                                                                              │
│  Nagz opens dashboard/index.html in a browser; approves competitors/niches   │
│  via a tiny local form that writes to data/decisions.json                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 9.1 Components
| Component | Tech | Responsibility |
|-----------|------|----------------|
| `collect` | Python 3.12, `google-api-python-client`, `google-auth-oauthlib`, `youtube-transcript-api` | Talk to YouTube, respect quota ledger, write raw rows |
| store | SQLite (stdlib `sqlite3`), schema migrations as numbered `.sql` files | Single source of truth |
| `packet` | Python | Build compact JSON packets for Claude from SQLite |
| analyse | `claude -p` via PowerShell/Python subprocess | Content analysis, niche brainstorm, step-tagging |
| `score` | Python | Implements §6 exactly; pure functions, unit-tested |
| `dashboard` | Python → single static HTML (Jinja2 template, Chart.js from a local vendored copy) | Read-only view + approve/reject forms |
| scheduler | Windows Task Scheduler + `run_weekly.ps1` | Orchestration; logs to `logs/` |

Static HTML was chosen over a web server because there is one user, on one machine, and a file that always opens is more reliable than a service that must be running. The approve/reject forms POST to a tiny `python -m ytscout serve` helper that only needs to be running while Nagz is reviewing; the dashboard also works read-only without it.

### 9.2 Repo layout
```
YTScout/
├─ DESIGN.md                  ← this file
├─ CLAUDE.md                  ← context for Claude Code (summary of this doc + conventions)
├─ README.md                  ← setup steps
├─ pyproject.toml
├─ issues/                    ← the work board: NNN-*.md open, closed/, stuck/ (§15)
├─ lazyboy/                   ← the unattended runner that works the board (§15)
├─ config/
│  ├─ settings.example.yaml   ← copied to settings.yaml (gitignored)
│  ├─ scoring.yaml
│  ├─ rpm_tiers.yaml
│  ├─ production_steps.yaml   ← step list + default hours (§6.4)
│  ├─ pipeline_coverage.yaml  ← output of the pipeline audit (§11, M3)
│  └─ seed_niches.yaml
├─ prompts/
│  ├─ video_summary.md
│  ├─ competitor_analysis.md
│  ├─ niche_brainstorm.md
│  └─ niche_step_tagging.md
├─ schemas/                   ← JSON Schemas matching each prompt
├─ docs/                      ← pipeline-audit.md, sensitivity.md (generated reports for Nagz)
├─ src/ytscout/
│  ├─ cli.py                  ← doctor | auth | collect | discover | packet | analyse | score | scout | dashboard | serve | audit
│  ├─ settings.py
│  ├─ youtube/                ← Data API + Analytics API clients, transports, quota ledger, cache, oauth
│  ├─ collect/                ← own, competitors, discover, analytics, transcripts, niches
│  ├─ scout/                  ← propose, validate, tag, score, snowball, sensitivity
│  ├─ transcripts.py
│  ├─ store/                  ← db.py, repo.py, migrations/*.sql
│  ├─ packets.py
│  ├─ claude_runner.py        ← subprocess wrapper around `claude -p`
│  ├─ scoring/                ← metrics.py, opportunity.py, money.py, effort.py, final.py (pure)
│  └─ dashboard/              ← templates/, static/, build.py, serve.py
├─ scripts/                   ← run_weekly.ps1, install_task.ps1
├─ tests/
├─ data/                      ← gitignored: sqlite, packets, decisions.json
└─ logs/                      ← gitignored
```

CLI subcommands and what they do:

| Command | Purpose | Hits the Data API? |
|---------|---------|--------------------|
| `doctor` | Check every dependency; print nothing secret | 1 unit (skipped with `--offline`) |
| `auth` | OAuth consent for the Analytics API (`--status` to inspect) | no |
| `collect --own / --competitors / --analytics / --transcripts / --niches` | Pull data into SQLite | yes, except `--analytics` and `--transcripts` |
| `discover` | Find candidate competitors (≤ 20 searches) | yes |
| `scout propose / validate / tag / snowball / sensitivity` | The niche pipeline | `validate` and `snowball` do |
| `packet` | Write one analysis packet (debugging) | no |
| `analyse --summaries / --competitors` | `claude -p` calls | no |
| `score [--niches / --competitors / --all]` | Pure computation from the DB | no |
| `dashboard` | Build `dashboard/index.html` | no |
| `serve` | Localhost review server for approve/reject/track/shelve | no |
| `audit` | Validate and print the pipeline coverage tables | no |

Every command that can hit the Data API requires `--max-units N` or `--dry-run`. Exit codes: `0` ok, `1` error, `2` not implemented yet, `3` quota exhausted (resume tomorrow), `4` no OAuth token, `5` `claude` unavailable.

---

## 10. Data model (SQLite)

Core tables; columns abbreviated. All timestamps UTC.

- **channels** — `id, title, custom_url, country, created_at, uploads_playlist_id, role ('own'|'competitor'|'niche_sample'), status ('approved'|'rejected'|'watch'|null), first_seen, last_refreshed`
- **channel_snapshots** — `channel_id, captured_at, subs, view_count, video_count`
- **videos** — `id, channel_id, title, description, tags_json, published_at, duration_s, is_short, category_id`
- **video_snapshots** — `video_id, captured_at, views, likes, comments`
- **transcripts** — `video_id, language, text, source, status, fetched_at`
- **own_analytics** — `video_id, window_start, window_end, views, est_revenue_usd, rpm_usd, monetized_playbacks, avg_view_duration_s, avg_view_pct, impressions, ctr, sub_delta, traffic_json`
- **niches** — `id, format, topic, topic_category, label, status, source ('llm'|'snowball'|'seed'), created_at, queries_json, required_steps_json`
- **niche_channels** — `niche_id, channel_id, is_small, added_at`
- **niche_scores** — `niche_id, scored_at, opportunity, small_outlier_rate, newcomer_view_share, concentration, newcomer_monthly_views_p25/p50/p75, rpm_gbp, est_monthly_gbp, manual_hours_per_month, score, confidence_flags_json`
- **competitor_analyses** — `id, run_at, prompt_hash, schema_version, packet_path, result_json, status`
- **video_summaries** — `video_id, prompt_hash, summary_json, created_at`
- **quota_ledger** — `day_pacific, units_used`
- **runs** — `id, started_at, finished_at, kind, status, log_path`
- **channel_metrics** — `id, channel_id, computed_at, window, format, metrics_json` (appended per refresh; latest wins)
- **api_cache** — `key, etag, body_json, fetched_at` (ETag cache for the Data API)
- **collector_state** — `kind, key, done_at` (resume checkpoints across quota days)
- **decisions** — `id, kind, target_id, decision, decided_at` (approve/reject/watch clicks)
- **schema_migrations** — `version, applied_at` (one row per applied `store/migrations/NNNN_*.sql`)

12-month history comes from keeping snapshots, never overwriting: `channel_snapshots` and `video_snapshots` have triggers that refuse UPDATE and DELETE.

---

## 11. Milestones

Each milestone ends with something Nagz can open and use.

**M0 — Skeleton (½ day)**
Repo, `pyproject`, CLI stub, SQLite migrations, quota ledger, settings loading, `README` with Google Cloud project + API key setup steps. `ytscout collect --own` pulls Countdown Animal Kingdom's public uploads and stats.

**M1 — Competitor metrics (1–2 days)**
Discovery, approve/reject via dashboard, weekly refresh, all §4.4 metrics, static dashboard with side-by-side tables and 12-month view charts. OAuth for Analytics API; own-channel table populated. Task Scheduler job installed.

**M2 — Competitor content analysis (1 day)**
Transcripts, packets, `claude_runner`, `video_summary` + `competitor_analysis` prompts and schemas, findings rendered in the dashboard with evidence links. First real weekly run end-to-end.

**M3 — Pipeline audit (½ day)**
Read `C:\Users\nagaj\git\top-five-animals-1`, map each of the §6.4 steps to `automated | partial | manual` with notes, write `config/pipeline_coverage.yaml`. Nagz reviews and corrects it. This is the only step that needs the other repo.

**M4 — Niche Scout (2 days)**
Brainstorm prompt, snowball, validation collector, §6 scoring (unit-tested against hand-worked examples), ranked niche table in the dashboard with Shorts/long-form toggle, shelve/track controls, 12-month trend arrows.

**M5 — Hardening (ongoing)**
Transcript failure handling, quota-day-splitting, run logs surfaced in dashboard, `ytscout doctor` command that checks API key, OAuth token, `claude` login and PATH.

---

## 12. Setup prerequisites (for README)

1. Google Cloud project (the pipeline's, or a new `ytscout` one) → enable **YouTube Data API v3** and **YouTube Analytics API** → create an API key (Data API) and an OAuth desktop client (Analytics). Put the key in `.env` as `YT_API_KEY`; save the client JSON at `YT_CLIENT_SECRET_PATH` (`scripts/.secrets/client_secret.json`). (Issue 005.)
2. Python 3.12 on Windows; `python -m venv .venv`, `.venv\Scripts\python.exe -m pip install -e ".[dev]"`.
3. Claude Code installed on Windows and logged in with Nagz's subscription (`claude` on PATH; `claude -p "ping"` works). Version ≥ 2.1.259.
4. `.env` from `.env.example` (the four `YT_*` keys) and `config/settings.yaml` from its example: `usd_gbp`, `pipeline_repo_path`, `own_channel_id` if it differs from `YT_CHANNEL_ID`.
5. `ytscout auth` once, for the Analytics consent. (Issue 012.)
6. `ytscout doctor` passes.
7. `scripts/install_task.ps1` registers the weekly Task Scheduler job. (Issue 014.)

Secrets are never committed: `data/`, `logs/`, `config/settings.yaml`, `.env` and `scripts/.secrets/` are gitignored from day one.

---

## 13. Risks and open questions

| Risk / question | Mitigation / owner |
|-----------------|--------------------|
| RPM tier table is guesswork | Calibrate to real Shorts RPM; show p25–p75 bands and an "uncalibrated" badge; every row cites a source and a review date. Revisit quarterly. |
| `youtube-transcript-api` breaks or gets rate-limited | Cache, backoff, graceful degradation to titles/descriptions; the pipeline never hard-fails on transcripts. |
| Outlier thresholds (3×, 10k views, <10k subs, <12 months) are arbitrary | All in `scoring.yaml`; M4 includes a sensitivity check on 5 hand-picked niches. |
| Claude subscription usage limits on a heavy week | Per-video summaries are small; comparison calls are one per module; the runner records token usage from `--output-format json` so the dashboard can show weekly spend. |
| PC asleep at run time | Task Scheduler "wake to run" + "run as soon as possible after a missed start". |
| Quota cap hit mid-run | Ledger stops at 9,000 units; run resumes next day from where it left off; dashboard shows partial state. |
| Shorts duration definition drift | `is_short` is derived from the channel's format mix and duration ≤ 3 min; threshold in config. |
| Analytics API may not expose impressions/CTR for this channel | Verify in M1; columns are nullable. |
| Is "small channel" the right proxy for "newcomer opportunity"? | It's the best public proxy available. Track it alongside newcomer view share and concentration so no single number dominates. |
| Long-form has no data on the own channel yet | Long-form calibration = 1.0 until Nagz publishes long-form; flagged in the dashboard. |

---

## 14. Glossary

- **Niche** — a `format × topic` pair, e.g. `top5-countdown × dangerous-animals`.
- **Small channel** — `subs < 10k AND age < 12 months` at the time of the snapshot.
- **Outlier video** — views ≥ 3× the channel's own median over its last 30 uploads, above an absolute floor.
- **Opportunity** — the 0–1 composite in §6.1.
- **RPM** — revenue per 1,000 views (after YouTube's cut), the number that turns views into money.
- **Manual hours** — human time per video for production steps the pipeline does not cover.
- **Score** — est £/month ÷ manual hours/month. The only number that ranks niches.
- **Packet** — a compact JSON file built from SQLite and handed to Claude for analysis.
- **Pipeline** — the existing `top-five-animals-1` automation that produces Countdown Animal Kingdom videos.
- **Issue** — one session-sized, vertically sliced piece of work in `issues/` (§15).
- **AFK / Active** — an issue a session can finish alone / one that needs Nagz in the room (§15).

---

## 15. How the work gets done — `issues/` and `lazyboy/`

This document is the PRD. It is delivered through a board of small tickets and an unattended runner, so that each build session starts with a small, fresh context and the backlog moves while nobody is at the machine.

### 15.1 The board

`issues/NNN-short-title.md`, one file per session-sized slice, cut from §11 so that every issue ends with something runnable. Open work sits in `issues/`; finished work moves to `issues/closed/` with an `## Outcome` section; work that could not be finished waits in `issues/stuck/` with a `## Lazyboy` note. A directory listing is the whole status report. Conventions and the template are in `issues/README.md` and `issues/templates/issue.md`.

Each issue's header carries three machine-read fields:

- **Type**: `AFK` — a session can finish it with no input from Nagz; every decision is already in the Scope or is safe for the session to make and write down. `Active` — it needs Nagz: a Google consent screen, a Task Scheduler prompt, a batch of competitors to approve, a dashboard to judge by eye. The rule is *prefer AFK*: if a decision is the only thing making an issue Active, make the decision in the Scope.
- **Blocked by**: issue numbers that must be closed first. This is the dependency graph.
- **Add dirs**: directories outside the repo the session may read (the pipeline audit reads `top-five-animals-1` in place).

The initial board (32 issues) has 25 AFK and 7 Active. The Active ones sit exactly where real credentials or real judgement enter: Cloud project + API key (005), approving competitors (009), OAuth consent (012), registering the scheduled task (014), the first end-to-end run (018), correcting the pipeline coverage (020), the first real scout run (027).

### 15.2 The runner

`lazyboy/` runs one Claude Code session per AFK issue, lowest number first, only when its blockers are closed; verifies completion by state (file moved to `closed/`, HEAD advanced, worktree clean) rather than by the session's word; retries once; parks failures in `stuck/`; and moves on. `.\lazyboy\afk.ps1` runs the loop, `.\lazyboy\afk.ps1 --dry-run` prints the order and what is waiting on Nagz, `.\lazyboy\once.ps1 NNN` gives the same brief to an interactive session for Active issues. It uses the same subscription login as any `claude` session; there is no API key.

`lazyboy/guard.py` is a `PreToolUse` hook that holds this document's hard lines even when a session is wrong: no collector run without `--max-units` (and none above 3,000 per session), no scraper installs or direct youtube.com fetches, no `ANTHROPIC_API_KEY` or `claude --bare`, no reading secrets into the transcript, no `ytscout auth` / `serve` / Task Scheduler commands (those are Active), no pushes or history rewrites. `lazyboy/README.md` has the full list.

### 15.3 Budgets inside a session

Two shared resources: **Data API units** (10,000/day, most of it needed by the weekly job) and **Claude subscription usage**. An issue that needs real API calls or real `claude -p` calls says so in its Scope with a number; without that line the session works from fixtures and fakes. The guard enforces the mechanics; the Scope sets the number.

### 15.4 When the board runs out

The first real runs (018, 027) will produce new issues. They are numbered after the last one, kept vertical and session-sized, and go through the same loop. New work found in the middle of a session is written as a new issue, not folded into the current one.
