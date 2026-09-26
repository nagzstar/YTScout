# 027 — First real niche scout run: propose, validate five, score, review

**Type**: Active
**Blocked by**: 005, 024, 025
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: data/ytscout.sqlite, dashboard/index.html, config/seed_niches.yaml
**Milestone**: M4

## Why

The scout meets real data and its first opinion. Five niches, one day's quota, and your
judgement on whether the ranking is sane.

## Scope

Pick a day the weekly job will **not** run (validation of 5 niches ≈ 4,000–4,500 units).

1. ```powershell
   .venv\Scripts\python.exe -m ytscout scout propose --from-seeds
   .venv\Scripts\python.exe -m ytscout scout propose --count 30
   .venv\Scripts\python.exe -m ytscout dashboard
   ```
   Look at the "waiting" table. Edit `config/seed_niches.yaml` if you want different seeds.
2. Choose **5** proposed niches worth the units — a spread: your current one (the
   reference), two Shorts, two long-form. Note their ids.
   ```powershell
   .venv\Scripts\python.exe -m ytscout scout validate --niche <id> --max-units 1000   # ×5
   .venv\Scripts\python.exe -m ytscout scout tag
   .venv\Scripts\python.exe -m ytscout score
   .venv\Scripts\python.exe -m ytscout dashboard
   .venv\Scripts\python.exe -m ytscout serve
   ```
3. Read the Niches table. For each of the five: is the £/month band believable? Are the
   manual hours right? Does the opportunity flag match what you see when you open the
   sample channels? Track the ones worth watching, shelve the rest.

## Out of scope

- Fixing what you find — issues.

## Acceptance criteria

- [ ] Five niches have `niche_scores` rows; at least one is `tracking`.
- [ ] Outcome: the five labels with score / opportunity / £ band / h per month, your
      verdict on each in one line, total units spent, and the new issue numbers you
      created for anything wrong (dashboard, scoring, tagging).

## Notes

`once.ps1 027` for Claude alongside. Do not write revenue or RPM figures in the Outcome —
the £ *estimates* for niches are fine, your channel's actuals are not.

## Outcome (closed 2026-09-26)

Run on Saturday 2026-09-26; the weekly job runs Monday 03:00. Proposals were already in
the DB (35 rows, seeds plus one earlier brainstorm), so `propose` was not re-run. Five
niches validated at `--max-units 1000` each, one `scout tag` call, `score`, `dashboard`.

**Units spent: 2,811** (562 per niche, early stop after 4 searches each). One real
`claude -p` call (tag, 5 niches, prompt 3dc68b52c8f6).

| id | niche | score | opp | £/mo p50 | £ band p25–p75 | h/mo | decision |
|---|---|---:|---:|---:|---|---:|---|
| 3 | One-animal deep dives (longform) | 0.798 | 0.37 | 6.07 | 0.34–64 | 7.6 | track |
| 33 | Aviation incidents explained (longform) | 0.172 | 0.32 | 1.31 | 0.37–48 | 7.6 | track |
| 1 | Top 5 countdowns: dangerous animals (reference) | 0.004 | 0.15 | 0.09 | 0.03–0.19 | 22.0 | track |
| 20 | Top 5 countdowns: prehistoric giants | 0.004 | 0.13 | 0.08 | 0.00–0.29 | 22.0 | shelve |
| 14 | Top 5 countdowns: engineering disasters | 0.002 | 0.21 | 0.09 | 0.00–0.37 | 37.0 | shelve |

Verdicts, one line each:

- **3 deep dives**: hours right (1.9 h/video). £ band plausible for long-form at the
  animals tier. Sample led by NBC News, so the opportunity number is not yet trusted.
- **33 aviation**: hours right. Highest RPM tier of the five, which is the whole reason it
  ranks second. Sample is news outlets (BBC, CNN-News18, Times Now); tracked as a bet on
  the tier, not on the sample.
- **1 dangerous animals**: hours right (1.1 h/Short: the audit's 1.2 minus community,
  which the tagger never requires). Opportunity 0.15 is the lowest of the five, which is
  believable for a saturated niche. £ estimate rounds to zero; see 051.
- **20 prehistoric giants**: near-identical inputs to niche 1 and the scorer cannot tell
  them apart. Sample led by Cleo Abram. Shelved.
- **14 engineering disasters**: specific footage tagged correctly; 37 h/month is the
  right consequence of that. Lowest score. Shelved.

All five carry `uncalibrated` (Shorts) or `longform_uncalibrated`: the own channel's
analytics windows hold views but no RPM in the last 90 days, so calibration is 1.0.

Issues opened:

- **049** relevance filter for validated niche channels (the polluted samples above).
- **050** verify why the money model is uncalibrated (monetisation vs collector bug).
- **051** decide what the £/month estimate anchors on (p50 newcomer views rounds every
  Shorts niche to £0).
- **052** `docs/pipeline-audit.md` and `effort.py` disagree on specific-footage costing
  (2.5 h full vs 1.25 h scaled override).
