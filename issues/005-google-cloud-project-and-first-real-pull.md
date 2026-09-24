# 005 — Google Cloud project, API key, first real pull of Countdown Animal Kingdom

**Type**: AFK
**Blocked by**: 004
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: config/settings.yaml (gitignored), data/ytscout.sqlite (gitignored)
**Milestone**: M0

## Why

Nothing real exists until a key does. The browser half (Cloud project, key, `.env`) was
done by Nagz on 2026-09-24; what remains is running the tool for real and recording what
the API returned. After this, every AFK issue that needs a small real call can have one.

## Already in place (do not redo)

- Cloud project: **the pipeline's `countdown-animal-kingdom` is reused.** One project for
  this tool, ever. Quota is shared with the pipeline's uploads (1,600 units each), so the
  weekly job runs on a non-publishing day or `quota.daily_cap` is lowered.
- YouTube Data API v3 is enabled. An API key restricted to it is in `.env` as
  `YT_API_KEY`. `.env` holds only the four `YT_*` keys.
- OAuth desktop client JSON and a token are at `scripts/.secrets/`; the token already
  carries `yt-analytics.readonly`. Consent flow itself is issue 012.
- `YT_CHANNEL_ID=UCJtxqVW4yQzvPzz6zYyaq3A` was verified with one `channels.list` call:
  title Countdown Animal Kingdom, 7 videos, uploads playlist `UUJtxqVW4yQzvPzz6zYyaq3A`.

## Scope

May spend up to **700 real Data API units**, always with `--max-units 300` per run.
No `claude -p` calls. Never read `.env` or `scripts/.secrets/`; `ytscout.settings` does.

1. `copy config\settings.example.yaml config\settings.yaml`. Leave `own_channel_id`
   unset so it falls back to `YT_CHANNEL_ID`.
2. Run, in order:
   ```powershell
   .venv\Scripts\python.exe -m ytscout doctor
   .venv\Scripts\python.exe -m ytscout collect --own --dry-run
   .venv\Scripts\python.exe -m ytscout collect --own --max-units 300
   .venv\Scripts\python.exe -m ytscout collect --own --max-units 300   # second run: snapshots grow, videos don't
   ```
3. Query the DB for the acceptance criteria and record the numbers in the Outcome.
4. If any command fails on a real API shape (a missing field, a duration form, a
   category id the fixtures did not cover), fix it here only if the fix is a line or two
   and covered by a fixture update; otherwise open a new issue and mark this one's
   Outcome with what is still unverified.

## Out of scope

- OAuth consent (012). Discovery (009). Changing the key, the project, or `.env`.

## Acceptance criteria

- [ ] `ytscout doctor` reports `YT_API_KEY: set` and `client secret: present` without
      printing either.
- [ ] After the first real run, `sqlite3 data\ytscout.sqlite "select count(*) from videos"`
      is > 0 and `select units_used from quota_ledger` is < 300.
- [ ] After the second run, `video_snapshots` has roughly twice the rows and `videos` the
      same count as before.
- [ ] `.env` and `scripts/.secrets/` are not in `git status` and never were
      (`git check-ignore -v scripts/.secrets/token.json` names a `.gitignore` line).

## Notes

In the Outcome, record: how many videos the channel had, units each real run cost, and
anything the API returned that surprised you (a missing field, a duration shape). Do not
paste the key, the secret, or the channel's revenue anywhere in the repo.
