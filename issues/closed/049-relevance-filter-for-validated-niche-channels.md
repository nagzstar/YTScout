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
