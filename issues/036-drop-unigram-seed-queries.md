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
