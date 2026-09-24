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

## Outcome (closed 2026-09-24)

Delivered: `config/settings.yaml`, copied from the example with `own_channel_id` left
empty so it falls back to `YT_CHANNEL_ID`. I wrote it because this issue's Scope asks for
it, even though sessions normally leave `settings.yaml` to Nagz. The file is gitignored.
Also delivered: a real `data/ytscout.sqlite` (gitignored), and new issue **033** for a
permanent TLS fix. No code changed. Real Data API units spent: **10 of the 700 allowed**.

What happened, in order:

- `doctor`: exit 0. It printed `YT_API_KEY: yes`,
  `client secret: scripts\.secrets\client_secret.json: yes`,
  `oauth token: ...token.json: yes` and `own_channel_id: yes`, with no values shown. The
  criterion's wording is "set" and "present", but the output says "yes". I treated that
  as met, because the meaning is the same and no secret was printed.
- `collect --own --dry-run`: 3 planned calls, 3 units, nothing written.
- **The first real run failed:** `SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED`.
  Avast Web/Mail Shield intercepts HTTPS on this PC and re-signs it with its own root.
  `httplib2` trusts only certifi, which does not contain that root. The run cost **1
  unit**, because the ledger charges before the call is made, and it left `runs` row 1
  with status `error`. A proper fix is more than two lines, so it is now issue 033.
- **The workaround:** `HTTPLIB2_CA_CERTS=<scratch>\ca.pem`, a scratch bundle made of
  certifi's `cacert.pem` plus `C:\ProgramData\Avast Software\Avast\wscert.pem`. This is
  a public CA certificate, not a secret. With it set, both real runs worked:
  - Run 2 (`--max-units 300`): exit 0, **3 units** (channels → playlistItems → videos).
    It stored 1 channel, **7 videos** and 8 snapshots (1 channel, 7 video).
  - Run 3 (`--max-units 300`): exit 0, **3 units**. `videos` stayed at 7,
    `video_snapshots` went from 7 to **14** and `channel_snapshots` to 2.
  - `quota_ledger`: one row, `('2026-09-23', 7)`. The ledger's day is Pacific time, so
    it is still the 23rd there. Total 7 units, well under 300.
- `git status` is clean. `git check-ignore -v` names `.gitignore:44:.secrets/` for
  `scripts/.secrets/token.json` and `.gitignore:14:.env` for `.env`.
  `git log --all -- .env scripts/.secrets` is empty, so neither has ever been committed.

What the real API returned:

- All 7 uploads are Shorts, from 51 s to 74 s. Every one has `categoryId` 15 (Pets &
  Animals) and tags. The durations were all simple `PT#S` / `PT#M#S` forms, and no field
  was missing, so the fixtures already covered every shape.
- The uploads run daily from 2026-09-17 to 2026-09-23, mostly at 11:00 UTC.

For the next session: every real API call from this PC needs `HTTPLIB2_CA_CERTS` set
until 033 is done. Build the bundle the same way (certifi plus Avast's `wscert.pem`). 013
and 014 (the weekly job) depend on 033 in practice, even though they do not list it.
