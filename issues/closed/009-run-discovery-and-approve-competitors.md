# 009 — Run discovery for real and approve the first competitor set

**Type**: Active
**Blocked by**: 005, 006, 008
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: data/ytscout.sqlite, data/decisions.json, config/scoring.yaml (discovery thresholds)
**Milestone**: M1

## Why

The first real 2,000 units, and the first time you look at the dashboard. What you approve
here is what 010 and 017 analyse; what you reject teaches the similarity filter.

## Scope

You, with `once.ps1 009` if you want Claude alongside.

1. Pick a day when the weekly job will not run (or before it is installed — 014).
2. ```powershell
   .venv\Scripts\python.exe -m ytscout discover --dry-run
   .venv\Scripts\python.exe -m ytscout discover --max-units 2500
   .venv\Scripts\python.exe -m ytscout dashboard
   .venv\Scripts\python.exe -m ytscout serve
   ```
   Open http://127.0.0.1:8765/ and go through the candidates.
3. Aim for **≥ 8 approved**, any number rejected, `watch` for "maybe later". Approve
   channels that actually compete for the same viewer, not just the same animals.
4. Look at the dashboard as a page: is anything unreadable, misleading, or missing? Write
   it down.

## Out of scope

- Tuning the filter in code — but if the candidates were mostly junk, say what was wrong
  with them so a follow-up issue can adjust `discovery:` thresholds.

## Acceptance criteria

- [ ] `select count(*) from channels where status='approved'` ≥ 8.
- [ ] `data/decisions.json` has one line per click.
- [ ] `quota_ledger` for the day shows the real cost of discovery (record it in the Outcome).
- [ ] The Outcome lists the approved channels (title + id), the rejection reasons in
      one line each, and every dashboard complaint — those become new issues (033+).

## Notes

If the run hits `QuotaExhausted` because something else spent the day's units, wait for
08:00 UK and run again; `discover` is idempotent.

## Progress (2026-09-24, session 1 — not closed)

State when the session ended: worktree clean at `463998e`; the review server is not
running (start it with `.venv\Scripts\python.exe -m ytscout serve`).

- **Quota**: `quota_ledger` 2026-09-24 = **2,423 units**. Run 1 (old filter) 1,211,
  run 2 (subs floor + viral hit) 1,211, plus 1 wasted on the TLS failure below.
- **Decisions so far**: 2 approved (Woofy D. Luffy `UCDlRCKlqeOSxvjhdzt839zw`, coco
  scene `UChItBtMVGAl-zBMcAyZoURg`), 31 rejected, 1 undecided. `decisions.json` has 37
  lines; the first (`watch` on Fact SL) was a mis-click, the four duplicate `rejected`
  lines for Fact SL came from a browser retry (fixed, see below). Both stay in the log.
- **Filter rewritten during the session, at Nagz's direction** (`config/scoring.yaml`
  `discovery:`, DESIGN.md §4.3 updated): the 0.1×–10× size band is gone. A competitor
  now needs ≥ 10,000 subs (no cap), one hit with ≥ 100,000 views, a topic word (animal
  list) in channel title/description/hit titles, and English (video language tags, else
  ≥ 90 % Latin-script hit titles). `search.list` gets `relevanceLanguage=en`. The
  **English + topic screens have not yet had a real run**; run 2 predates them.
- **Also fixed here**: channel links in the dashboard; `serve` answers a rebuild failure
  with JSON (a dropped connection made the browser retry the POST and double-record);
  a 2 s idle-socket timeout (a browser preconnect stalled every request by ~7 s); CLI
  stdout/stderr use `errors="replace"` (an emoji in a channel title crashed
  `discover` after it had committed).
- **TLS**: every real run still needs `HTTPLIB2_CA_CERTS` pointing at certifi +
  `C:\ProgramData\Avast Software\Avast\wscert.pem` (issue 033). Rebuild the bundle in
  the scratchpad; do not commit it.
- **New issues**: 034 (seed queries need a topic word; `viral`/`vs`/`versus` are half the
  queries and cost 600 units a run for junk), 035 (undo a decision; decided rows
  visible; bulk `ytscout decide`). Permission blocked the session from bulk-rejecting
  the 19 sub-10k leftovers; Nagz clicked them instead.

Next session: decide whether to rerun `discover --max-units 1300` today (≈1,225 units,
~1,150 over this issue's 2,500 grant but far under the 9,000 cap) or after 08:00 UK,
ideally after 034. Then approve towards ≥ 8, and close with the Outcome: approved list,
rejection reasons, real cost, dashboard complaints (no undo; restart `serve` after code
changes; Hits column needs a tooltip saying "search hits"; emoji titles render fine).

## Progress (2026-09-24, session 2 — not closed)

State when the session ended: worktree clean at the commit after `389c840`; dashboard
rebuilt with 10 undecided candidates; the review server is not running.

- **034 done first** (closed, `f563826`): hype words stripped, so the seeds are now
  `top 5 land animals`, `top 5 animals`, `top 5 land` — 3 queries, ~607 units a run.
- **Run 3** (12:45, 607 units): 221 hit channels, **0 new kept**. Replayed offline from
  `api_cache`: with only two content words across the seeds, `keyword_overlap_min: 2`
  meant "every query word", and the 8 channels that passed it were the 2 approved plus 6
  real animal channels whose best hit was under 100,000 views.
- **Threshold change** (`config/scoring.yaml`, `389c840`): `keyword_overlap_min: 1`. The
  `topic_words` screen is the subject gate; overlap now only asks for one shared word.
- **Run 4** (13:0x, 607 units): 221 hit channels, **12 kept** = 2 approved + **10 new**:
  Top 5 Animal voice overs `UChCsRA41E4s-7vg-p2gP_iw`, LOWLIGHTS
  `UCoImDDWjNbqX5SLNpFMVR8g`, CritterClipzLOL `UCSJPhVc02KDgAD4C1eM5lNQ`, Rufus Goodboy
  `UC6T4TmdMIg3bPWd9at6vFRQ`, Tovo Ranks `UCJZdt-ER-peWnO4fnEhxRiQ`, Beast tier
  `UCAXr04ES-_yMS24N-H4MQQA`, RankingvideosFunny `UCYTWqfll2WNqsKB2FXK7sgA`, AstroFact
  `UCjB3HIgZAWu9lqOEfCpv1Jw`, Curious Bone `UCjot6Wj8aZqpHDZGJ1-_12Q`, CT_seeking
  `UCwU15FLyzh0xArC398Mk4jw`. All ≥ 10k subs, 100 % Shorts hits, a hit ≥ 1.8M views,
  English by tag. Dropped 209: 121 under 10k subs, 39 off topic, 17 no shared word,
  16 rejected before, 12 no viral hit, 5 not English, 2 not Shorts.
- **Quota**: `quota_ledger` 2026-09-24 = **3,637 units** (runs 1–4: 1,211 + 1,211 + 607
  + 607, +1 wasted), against this issue's 2,500 grant. The overrun is the two runs the
  filter rewrite needed.
- **`top 5 land`** is still a junk query (200 units for NFL rankings and Vietnamese
  property): a unigram that is not a topic word makes a poor seed. Candidate for a new
  issue if the weekly refresh keeps it.

Next: Nagz runs `.venv\Scripts\python.exe -m ytscout serve`, reviews the 10 at
http://127.0.0.1:8765/ (≥ 6 more approvals reach the 8), notes dashboard complaints, then
the Outcome closes the issue.

## Outcome (closed 2026-09-24)

Closed at **7 approved**, one short of the 8 target, at Nagz's decision: the filter now
produces real competitors, and 010/017 can start on seven while the weekly refresh (014)
surfaces more. The acceptance criteria are marked as they stand.

- [ ] `channels` approved = **7** (target 8; accepted as-is).
- [x] `data/decisions.json` has 47 lines, one per click (including the mis-click and the
      four browser-retry duplicates noted in session 1; both stay in the log).
- [x] `quota_ledger` 2026-09-24 = **3,686 units** for the whole issue: four real discovery
      runs (1,211 + 1,211 + 607 + 607), the rest on the TLS failure and dashboard
      rebuilds. The issue's grant was 2,500; the overrun is the two runs the filter
      rewrite needed.
- [x] Approved, rejected, and dashboard complaints below.

### Approved

| Channel | id |
|---|---|
| Woofy D. Luffy | `UCDlRCKlqeOSxvjhdzt839zw` |
| coco scene | `UChItBtMVGAl-zBMcAyZoURg` |
| LOWLIGHTS | `UCoImDDWjNbqX5SLNpFMVR8g` |
| CritterClipzLOL | `UCSJPhVc02KDgAD4C1eM5lNQ` |
| Beast tier | `UCAXr04ES-_yMS24N-H4MQQA` |
| AstroFact | `UCjB3HIgZAWu9lqOEfCpv1Jw` |
| Curious Bone | `UCjot6Wj8aZqpHDZGJ1-_12Q` |

### Rejected (36)

Nagz's summary: almost all were not competition — not English, not animals, or too small.
By batch:

- **Runs 1–2 (31 rejected)**: produced by the old 0.1×–10× size band before the subs floor,
  viral-hit, topic-word and English screens existed. 19 were under 10k subs; the rest
  were off-topic (general facts, gaming, NFL/property rankings) or non-English.
- **Run 4 (5 rejected)**: Top 5 Animal voice overs, Rufus Goodboy, Tovo Ranks,
  RankingvideosFunny, CT_seeking — passed every automatic screen but do not compete for
  the same viewer (single-animal pet content, generic ranking formats, or facts channels
  where animals are incidental).

What this teaches the filter: the four screens added in this issue (subs ≥ 10k, one hit
≥ 100k views, topic word, English) are the right gates; the residual rejections are
editorial ("same animals, different viewer") and belong with a human, not a threshold.

### Dashboard complaints → issues

- No undo, decided rows vanish, no bulk decide → **035** (already filed).
- `top 5 land` is a junk seed query (200 units a run for NFL and property rankings) →
  **036**.
- Hits column needs a tooltip saying "search hits"; `serve` must be restarted after code
  changes (no reload) → **037**.
- Emoji in channel titles render fine after the `errors="replace"` fix.
- Every real run needs the TLS bundle → **033** (already filed).
