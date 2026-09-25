# 036 — A seed query needs a topic word, not just a content word

**Type**: AFK
**Blocked by**: 034
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/collect/discover.py (seed query builder), config/scoring.yaml, tests
**Milestone**: M1

## Why

After 034 the seeds are `top 5 land animals`, `top 5 animals`, `top 5 land`. The third
is built from a content unigram that is not a topic word, and in 009 it cost 200 units
per run for NFL rankings and Vietnamese property listings — a third of every weekly
discovery spend for nothing.

## Scope

- A seed query must contain at least one `topic_words` entry (the animal list in
  `config/scoring.yaml`). Queries that do not are dropped before `search.list`.
- `discover --dry-run` prints the seeds it would use and the ones it dropped, with why.
- Unit test with the current seed set: `top 5 land` is dropped, the other two survive.

## Out of scope

- Adding new seed sources. No real API units.

## Acceptance criteria

- [ ] `discover --dry-run` lists two queries, not three, and names the dropped one.
- [ ] pytest covers the drop rule.

## Notes

See 009 Outcome and the 034 close for how the seeds are built today.

## Outcome (closed 2026-09-25)

Delivered `screen_seeds(queries, topic_words)` in `src/ytscout/collect/discover.py`. A seed
survives only if its `tokens()` share a word with `discovery.topic_words` in
`config/scoring.yaml`. Every other seed is recorded in `DiscoveryResult.dropped_queries` as
`(query, "no topic word")`. The screen runs on the uncapped seed list before the
`--max-searches` cap, so a dropped seed never takes a surviving seed's slot. `config/scoring.yaml`
needed no change because the topic list was already there.

Verified by running `discover --dry-run` against the real DB. It now lists 2 queries
(`top 5 land animals`, `top 5 animals`) and prints `dropped seeds (1): top 5 land (no topic
word)`. The plan is 4 searches, 400 units, down from 600. No real units were spent.

Tests: `test_seed_without_a_topic_word_is_dropped` covers the current seed set, and
`test_dropped_seeds_do_not_take_a_query_slot` checks the order of screening and capping. The
fixture dry-run test now expects 6 queries instead of 8, because `top 5 dangerous` and
`top 5 deadliest` also drop. That is intended: adjectives are not topics.

Next: a seed that is only a channel tag (for example `wild animals`) passes when it holds a
topic word, as before. If the topic list grows, the number of surviving seeds grows with it.
