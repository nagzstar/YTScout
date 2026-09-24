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
