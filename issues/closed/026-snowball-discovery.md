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

## Outcome (closed 2026-09-25)

Delivered in commit 93f3a79 (plus this close-out). No real API units spent.

- `ytscout scout snowball [--max-searches N] [--max-units N] [--dry-run]` in
  `src/ytscout/scout/snowball.py`, wired in `cli.py` (no longer a stub). Default 10
  searches; `--max-searches 16` is refused with exit 1 (checked). Like every collector it
  needs `--max-units` or `--dry-run`.
- Dry run on the real DB: 10 queries from the approved competitors (e.g. `top 5 fastest
  animals`, `top 5 sea creatures`, `animals diets`), **estimate <= 1,014 units**.
- **Sources**: approved competitors plus channels of niches with status `track` or
  `tracking` (the dashboard stores `track`). No niche is tracked yet, so today only
  competitors feed it. Competitors have no category column; they are taken as
  `animals_nature` (the own channel's category). Queries go round-robin by rank, the
  source with the most-viewed video first, so one source cannot fill the cap.
- **Queries** (`text.snowball_query`): the two most frequent content words of
  `tokens(title)` (which also strips 034's format tokens such as `viral`, `vs`,
  `compilation`), in title order, prefixed `top 5 ` when the title said top 5 / top five /
  top5. Titles with no content word give no query. Compared to existing niche queries
  lower-cased with whitespace collapsed.
- **Cheaper than the letter of the Scope, same result**: channels with < 3 hits are
  dropped *before* `channels.list` (which then only confirms they exist), and
  `videos.list` covers only the clustered channels' hit videos. Worst case with N
  searches is `N×100 + ceil(⌊50N/3⌋/50) + N` (1,014 for 10, 1,520 for 15). A real run is
  cut to the searches whose worst case fits under `--max-units` and today's cap, exits 3
  when not even one fits, and writes nothing when Google stops it midway.
- Only `niches` rows are written: no `channels`/`videos` rows for the found channels (022
  samples them properly later). New migration **0008**: `niches.seed_json`, the cluster's
  channel ids. `meta_json` holds `hit_count`, `keywords`, `shorts_share`,
  `category_fallback`. Topic slug and label come from the top two keywords in rank order
  (`deadliest-snakes`, "Deadliest snakes"). A cluster whose `(format, topic)` already
  exists is skipped and reported.
- Thresholds (3 hits, Jaccard 0.3, clusters ≥ 3, 70 % Shorts, 3 source videos) are module
  constants in `snowball.py`, fixed by this issue; they are discovery heuristics, not
  scoring. Lookback (365 d) and `relevanceLanguage` reuse `niche_validation` in
  `scoring.yaml`; the Shorts cut-off is `shorts_max_seconds` (180).
- Tests: `tests/test_scout_snowball.py`, 23 tests. The fixture has 2 sources, 6 queries,
  2 clusters (Shorts at exactly 75 %, long-form at 2/9), noise (a lone cooking channel, a
  2-hit channel that would otherwise join a cluster) and two known channels that would
  otherwise join; exactly 2 proposals, **602 units** asserted. Suite 461 passed; ruff
  clean. DESIGN.md §5.2 notes the as-built behaviour.
- **For the next issue**: `scout validate` does not read `seed_json` yet; it still starts
  from the niche's search queries. Reusing the seeds (to skip or shrink searches) is a new
  issue if wanted.
