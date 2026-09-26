# 049 — Filter off-topic channels out of a niche's validation sample

**Type**: AFK
**Blocked by**: 027
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/scout/validate.py, src/ytscout/scoring/niches.py, config/scoring.yaml, tests/
**Milestone**: M5

## Why

The first real scout run (027) sampled 80 channels per niche straight from `search.list`
and the samples are polluted. "Aviation incidents explained" is led by BBC News,
CNN-News18 and Times Now; "One-animal deep dives" by NBC News; "Top 5 dangerous animals"
by Kurzgesagt and unrelated Indian entertainment channels; "Prehistoric giants" by Cleo
Abram. The step tagger itself wrote "sample titles are off-topic" for two of the five.
Concentration, newcomer view share and the small-channel outlier rate are all computed
over those channels, so every opportunity number from 027 is suspect.

## Scope

- Add a relevance gate between "channel returned by a search" and "channel counted in
  the niche". Reuse what discovery already has (`discovery.language`,
  `latin_share_min`, keyword overlap against the niche's queries and label) rather than
  inventing a second filter; put any new thresholds in `config/scoring.yaml` under
  `niche_validation`.
- News outlets and general-interest mega-channels should drop out. A channel whose recent
  video titles share no keyword with the niche's queries is not in the niche.
- Filtered channels stay in `niche_channels` with a reason column or flag so the
  dashboard can show what was excluded; the scorer ignores them.
- Re-score the five 027 niches from the stored data (no API units) and put the before
  and after opportunity numbers in the Outcome.
- No real API units. No real `claude -p`.

## Out of scope

- A Claude relevance pass per channel. Try the keyword gate first; open another issue if
  it is not enough.
- Re-validating the niches.

## Acceptance criteria

- [ ] `ytscout score` on the 027 data excludes the news outlets named above from all five
      niches, and the dashboard's niche detail shows the excluded count.
- [ ] Tests cover: an English topical channel passes, a news outlet fails, a channel with
      no overlapping keywords fails.
- [ ] Outcome: before/after opportunity for niches 1, 3, 14, 20, 33.

## Notes

`DESIGN.md §6.2` defines the opportunity inputs. The discovery filter is in
`src/ytscout/collect/discover.py`. 027's Outcome lists the offending channels.

## Outcome (closed 2026-09-27)

**Delivered.** A relevance gate between "a search returned this channel" and "this
channel counts in the niche" (`src/ytscout/scout/relevance.py`). `score --niches`
screens every channel linked to every sampled niche (shelved ones too) on the titles and
categories of its newest 30 stored videos. A channel that fails keeps its `niche_channels`
row with the new `excluded_reason` column set (migration 0012), and `build_sample` leaves
it out. The ranked table has a `kept` column (`46/80`), and the dashboard's niche detail
has an "Excluded from the sample (N)" block that lists each channel and its reason.

**Checks, first failure wins** (thresholds in `niche_validation.relevance`):
1. News outlet: at least 50% of videos in category 25 (News & Politics).
2. Script: titles under `discovery.latin_share_min` (0.9) Latin letters. This reuses the
   discovery setting.
3. English (runs only when `discovery.language` is `en`): at least 20% of titles contain a
   non-English function word (le/der/que/não/…) and those titles outnumber the ones with
   an English function word. Stored videos have no language tag, so this stands in for
   discovery's tag check. Titles made only of hashtags are not counted as foreign.
4. Topic: at least 10% of titles share a `text.tokens` word with the niche's queries,
   label or topic. Zero shared words always fails. This check is what removes
   general-interest mega-channels: Kurzgesagt matched 2 of 30 titles and Cleo Abram 2 of 30.

A channel with no stored videos is kept, because there is no evidence either way.

**Decisions.**
- The gate runs at score time on stored data, not inside `scout validate`. It costs no
  units, it re-screens whenever the thresholds change, and it covers niches that were
  validated before this issue. `validate.py` is unchanged, even though Covers lists it.
- `scoring/niches.py` in Covers does not exist. The pure check lives in
  `scout/relevance.py`, because it reuses `collect.discover.latin_share` and
  `ytscout.scoring` cannot import `collect` (that would be a circular import).
- `score_niches(relevance=None)` keeps the stored verdicts. The CLI (`score`, and
  `collect --niches` through `_score_niches`) passes the config.
- Test fixture video titles in `test_scout_score.py` now contain "animals", so the 023
  worked example stays topical under the gate. With the gate on and a news outlet added,
  the worked example scores 0.387 again (`tests/test_relevance.py`).

**Acceptance.**
- A real `ytscout score --niches` on the 027 DB (no API units, no `claude -p`) excluded
  BBC News, Times Now and CNN-News18 (33), NBC News (3), Kurzgesagt (1) and Cleo Abram
  (20). It also excluded KING 5 Seattle and The Weather Channel (14), plus tagesschau,
  FRANCE 24 English, Bloomberg, NewsX and others (33). `ytscout dashboard` shows
  "Excluded from the sample (N)" for all five niches: 34, 18, 34, 19 and 53.
- Tests cover the three cases the issue asks for (English topical passes, news outlet
  fails, no keyword overlap fails). They also cover the mega-channel, non-Latin and French
  cases, the config errors, shelved niches, re-screening and the dashboard render.

**Before and after.** Both columns are for the same code with the gate off and on,
computed on throwaway copies of the DB at the same moment. The "after" figures for 1, 3
and 33 match the real CLI run.

| niche | kept | opportunity | small outlier rate | newcomer view share | concentration | est £/mo | score |
|---|---|---|---|---|---|---|---|
| 1 dangerous animals (shorts) | 80 → 46 | 0.151 → 0.092 | 0.077 → 0.100 | 0.001 → 0.003 | 0.439 → 0.791 | 0.09 → 0.07 | 0.004 → 0.003 |
| 3 one-animal deep dives (long) | 80 → 62 | 0.369 → 0.358 | 0.514 → 0.500 | 0.093 → 0.093 | 0.581 → 0.598 | 6.07 → 4.85 | 0.798 → 0.638 |
| 14 engineering disasters (shorts) | 80 → 46 | 0.212 → 0.059 | 0.105 → 0.038 | 0.331 → 0.057 | 0.699 → 0.884 | 0.09 → 0.08 | 0.002 → 0.002 |
| 20 prehistoric giants (shorts) | 80 → 61 | 0.133 → 0.182 | 0.136 → 0.176 | 0.003 → 0.007 | 0.679 → 0.540 | 0.08 → 0.16 | 0.004 → 0.007 |
| 33 aviation incidents (long) | 80 → 27 | 0.323 → 0.233 | 0.385 → 0.357 | 0.028 → 0.048 | 0.386 → 0.800 | 1.31 → 1.58 | 0.172 → 0.209 |

Reading: niche 14 was flattered by off-topic small channels. Once they go, its newcomer
view share falls from 0.33 to 0.06. Niche 33's concentration doubles once the news
outlets and foreign-language channels leave, so only 27 channels remain.

**For the next issue.**
- 14 and 20 are shelved, so `score` screens them but does not append a score row. The
  dashboard therefore shows their old (pre-gate) score beside the new excluded list. The
  after numbers above for 14 and 20 come from calling `score_niche` directly.
- Taking Off (aviation, 263k subs) is excluded because every one of its videos is
  categorised News & Politics. If aviation niches matter, consider a topical override
  for news-category channels with a high topical share.
- About 20 Indian channels with mixed Hindi/Latin titles (Latin share 0.6–0.89) drop out
  on the script check. That matches discovery's rule for competitors.
- Unverified by eye: nobody has looked at the dashboard's new block in a browser. The
  test only checks the rendered HTML.
- 053 (the second scout run) will be screened automatically when it is scored.
