# 034 — Seed queries need a topic word, and "viral"/"vs" are not topics

**Type**: AFK
**Blocked by**: 009
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/text.py (STOP_WORDS, seed_queries, query_terms), config/scoring.yaml (discovery), tests/test_discover.py
**Milestone**: M1

## Why

The two real discovery runs in 009 built these six queries from the own channel's seven
titles and tags: `top 5 viral versus`, `top 5 land animals`, `top 5 viral`, `top 5
animals`, `top 5 vs`, `top 5 versus`. Half carry no topic at all. The keyword-overlap
check then counted `viral` and `vs` as overlap, so gaming, cricket and edit channels
passed as "animal" competitors: 7 of the 10 candidates from the second run matched only
`viral, vs`. Each junk query costs 200 units. Fixing the seeds is worth more than any
threshold change.

## Scope

- Add `viral`, `vs`, `versus`, `shorts`, `short`, `video`, `videos` and the other
  format/hype words that appear in the real titles to `STOP_WORDS` (or a separate
  `FORMAT_WORDS` set that `tokens()` also strips), so they neither seed queries nor count
  as overlap. Keep the list explicit and tested; do not lemmatise.
- `seed_queries` emits a query only if it contains at least one content word after the
  strip. `top 5` alone is never a query.
- `query_terms` therefore never contains a stop word, and `keyword_overlap` cannot be
  satisfied by one.
- Record the resulting queries for the real DB with `discover --dry-run` in the Outcome.
- No real API units. Fixtures only. The next real run is 010's weekly refresh or a rerun
  of 009 by Nagz.

## Out of scope

- The subscriber floor and the viral-hit screen (done in 009). Snowball discovery (026).

## Acceptance criteria

- [ ] `tokens("Top 5 Viral Animals vs Humans #shorts") == ["animals", "humans"]`, asserted.
- [ ] With the real own titles, `discover --dry-run` prints no query without a content word.
- [ ] A test asserts that a candidate whose only overlapping words are `viral` and `vs`
      is dropped with `keyword overlap 0 < 2`.
- [ ] `pytest -q` passes; `ruff` clean.

## Notes

The own channel's tags (`brandingSettings.channel.keywords`) are the other seed source;
if they are the origin of `viral` and `versus`, say so in the Outcome so Nagz can change
the tags on the channel too.

## Outcome (closed 2026-09-24)

Done inside the 009 session, before its third real run, so the run would not spend
600 units on `top 5 viral` and friends.

- `FORMAT_TOKENS` in `src/ytscout/text.py` now also holds `short`, `video`, `videos`,
  `clip`, `clips`, `compilation`, `viral`, `trending`, `vs`, `versus`. `tokens()` strips
  them, so they neither seed a query nor count as keyword overlap. `STOP_WORDS` is
  unchanged (still the 100 tested words). No lemmatising.
- `seed_queries` already built n-grams only from `tokens()` output, so once the hype
  words are stripped `top 5` alone cannot be emitted; a tag such as `viral` is skipped
  like `shorts` was. New tests: `test_tokens_strip_hype_and_format_words`,
  `test_seed_queries_never_emit_top_5_alone_or_a_hype_word`,
  `test_hype_words_alone_never_satisfy_keyword_overlap` (`keyword overlap 0 < 2 (-)`).
  The existing off-topic test now overlaps on `moments, reality` instead of `viral, vs`.
- Origin of `viral`/`versus`: the own titles' hashtags (`#Shorts #Viral #Versus`,
  `#Top5`), not `brandingSettings.channel.keywords` — the dry run shows no tag-derived
  query at all, so the channel keywords are empty or all format words. Nothing to change
  on the channel, though a topic hashtag (`#Animals`) per video would seed better queries.
- `discover --dry-run` against the real DB now plans **3** queries: `top 5 land animals`,
  `top 5 animals`, `top 5 land` — 6 searches, 601 units, worst case 613 (was 6 queries,
  1,201 / 1,225). `top 5 land` is weak but carries a content word; better seeds need more
  own titles or a topic hashtag, not code.
- `pytest -q`: 338 passed; `ruff format` / `ruff check` clean. No API units spent.
