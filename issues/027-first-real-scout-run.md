# 027 — First real niche scout run: propose, validate five, score, review

**Type**: Active
**Blocked by**: 005, 024, 025
**Add dirs**: none
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
