# 026 — `scout snowball`: adjacent niches from channels we already know

**Type**: AFK
**Blocked by**: 022
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: src/ytscout/scout/snowball.py, src/ytscout/text.py, tests/test_scout_snowball.py
**Milestone**: M4

## Why

The brainstorm proposes what Claude can imagine; snowballing proposes what the data
already touches. Cheap, bounded, and it finds the niches next door.

## Scope

- `ytscout scout snowball [--max-searches N] [--max-units N] [--dry-run]`, default
  `--max-searches 10` (1,000 units); CLI refuses > 15.
- Sources (zero cost — already in the DB): every `approved` competitor and every channel
  of a `tracking` niche. From each, its top 3 videos by latest views.
- Queries: one per source video title via `text.py` (lower-case, strip numerals and the
  stop list, keep the two most frequent content words + `top 5` if the title had it).
  De-duplicate; drop any query already used by an existing niche; cap at `--max-searches`.
- For each query: one `search(order=viewCount, published_after = now − 365 d)`. Collect
  channels **not** in any `niche_channels` row and not a tracked competitor.
  `channels(ids)` → drop those with < 3 hits across all queries.
- Cluster the remaining channels by title-keyword overlap: Jaccard ≥ 0.3 on their hit
  titles' content-word sets, single-linkage, drop clusters of < 3 channels.
- Each cluster → a proposed niche: `format` = shorts if ≥ 70 % of the cluster's hit
  videos are ≤ 180 s (needs `videos(ids)` on hits — budget it), `topic` = the cluster's
  top two keywords slugified, `label` from the same, `topic_category` = the category of
  the source niche/competitor most represented in the cluster (fallback `entertainment_pop`,
  flagged), `example_search_queries` = the 3 cluster queries with most hits, `source =
  'snowball'`, `status='proposed'`. Store the seed channel ids on the niche row
  (`seed_json`) so 022 can reuse them.
- Fixtures + tests: 2 source channels, 6 queries, search results forming 2 clear clusters
  and noise → exactly 2 proposals with the expected slugs and formats; unit total equals
  the hand-computed number; already-known channels excluded.

## Out of scope

- Validating what it proposes (that is 022, on the next run).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_scout_snowball.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout scout snowball --dry-run` prints the queries and
      a unit estimate ≤ 1,500.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §5.2` feed 2. No real API call in this session.
