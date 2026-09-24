# 017 — `analyse --competitors`: the comparison call and the Findings section

**Type**: AFK
**Blocked by**: 016
**Add dirs**: none
**Model**: claude-fable-5-1 high
**Covers**: prompts/competitor_analysis.md, schemas/competitor_analysis.json, src/ytscout/packets.py (comparison packet), src/ytscout/collect/analyse.py, src/ytscout/dashboard/templates/findings.html.j2, tests/test_competitor_analysis.py
**Milestone**: M2

## Why

This is the answer to "what are they doing better than me, and what should I make next".
One call a week, grounded in per-video summaries and metrics, rendered with the evidence
one click away.

## Scope

- Comparison packet: `competitor_packet(conn) -> dict` with, for the own channel and each
  `approved` channel: title, subs, the latest `channel_metrics` (90 d, shorts), and the
  `video_summaries` for its last 15 summarised videos (each with video_id, title, views,
  published_at, and the summary fields). Cap total size at ~150 KB; if over, drop to 10
  videos per channel and note it in the packet's `meta`.
- `prompts/competitor_analysis.md` + `schemas/competitor_analysis.json`:
  - `per_competitor[]`: `channel_id`, `does_consistently[]`, `they_do_we_dont[]`,
    `we_do_they_dont[]` (each item ≤ 200 chars, each with `evidence_video_ids[]`)
  - `topic_gaps[]`: `topic`, `covered_by_channel_ids[]` (≥ 2), `evidence_video_ids[]`, `why`
  - `our_weaknesses[]`: `pattern`, `below_median_video_ids[]`, `above_median_video_ids[]`
  - `next_videos[]` exactly 5: `title`, `angle`, `evidence_video_ids[]`, `rationale`
  - `meta`: `confidence` (low/medium/high), `caveats[]`
  The prompt states the channel's format rules briefly (Top-5 countdown Shorts, faceless,
  no sign-off) so suggestions fit, and forbids inventing video ids not in the packet.
- `ytscout analyse --competitors`: build packet → `run` → validate every referenced
  `video_id` exists in the packet (drop any that do not, count them in `caveats`) → write
  a `competitor_analyses` row (`status='ok'`). On `ClaudeUnavailable` → row with
  `status='pending'`, exit 5.
- Dashboard **Findings** section: latest analysis, rendered as: next 5 videos (top),
  topic gaps, our weaknesses, then a card per competitor. Every `evidence_video_ids`
  entry is a link `https://www.youtube.com/watch?v=<id>` with the video's title as text
  (look it up; fall back to the id). Show `run_at`, `prompt_hash`, confidence, caveats.
  Show "analysis pending — `claude` was unavailable at HH:MM" when the latest row is pending.
- Update the 007 external-reference test if the whitelist needs `youtube.com/watch` —
  it should already allow it.
- Tests: with the fake `claude` returning a canned analysis that references one unknown
  video id — assert the row is written, the unknown id is dropped and counted, the
  dashboard renders 5 next-video titles and the evidence links; pending path on
  `ClaudeUnavailable`.
- **Real calls allowed**: up to **15** `claude -p` runs (summaries + comparison) to prove
  it end to end if a real DB with transcripts exists.

## Out of scope

- Acting on the suggestions. Long-form suggestions (the prompt may mention them in
  `caveats` but the schema stays Shorts-shaped for now).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_competitor_analysis.py`.
- [ ] `.venv\Scripts\python.exe -m ytscout analyse --competitors` on the fixture DB with the
      fake `claude` writes one `competitor_analyses` row and `ytscout dashboard` renders a
      Findings section with 5 suggestions.
- [ ] A test proves an evidence id absent from the packet never reaches the DB row.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §4.6`. Two-stage (summaries then comparison) is deliberate — keep it. Record the
real run's token usage in the Outcome if you made one; 031 will chart it.

## Outcome (closed 2026-09-24)

**Delivered.** `ytscout analyse --competitors` builds the comparison packet
(`packets.competitor_packet`), writes it as `data/packets/YYYY-MM-DD-competitors-<n>.json`,
runs `claude_runner.run` with `prompts/competitor_analysis.md` and
`schemas/competitor_analysis.json`, grounds the output (`analyse.ground_analysis`) and
appends a `competitor_analyses` row with `status='ok'`, `prompt_hash` and the schema hash in
`schema_version` (016's suggestion). `--dry-run` prints the packet's channels, video
counts and byte size and calls nothing. The dashboard's Findings section renders the latest
row: next 5 videos, topic gaps, our weaknesses, a card per competitor, with every cited id
a `https://www.youtube.com/watch?v=` link titled from `videos` (falling back to the id),
plus `run_at`, both hashes, confidence and caveats. A `pending` row renders as "analysis
pending — `claude` was unavailable at HH:MM UTC (reason)".

**Decisions.**
- **Grounding is a walk, and caveats carry counts only.** Every list under a key ending in
  `_video_ids` keeps only ids that are packet videos; every list under `_channel_ids` keeps
  only packet channels; a `per_competitor` entry with an unknown `channel_id` is dropped.
  The Scope says "count them in `caveats`", and naming the ids there would have put the
  unknown id into the row, which the acceptance criterion forbids. So the caveat says
  "N cited video id(s) were not in the packet and were dropped." and the CLI prints the ids.
  Grounding happens after schema validation, so a `topic_gaps` entry can end with fewer
  than two `covered_by_channel_ids`; the dashboard shows it anyway.
- **Packet channels = own + `approved`** (not `watch`), as the Scope says; summaries still
  cover `watch` channels (016), so promoting one to `approved` needs no re-summarising.
  Order is `repo.tracked_channels`: own first, then by title. Each channel carries the
  latest `90d`/`shorts` `channel_metrics` (`null` if never scored) minus `outlier_ids`, and
  a `views_median` computed from the packet's own videos so "below/above median" is
  unambiguous for the prompt. Each video carries the newest summary row by `created_at`,
  whatever its prompt hash. The size check is on the UTF-8 JSON (150,000 bytes); over it,
  the packet is rebuilt at 10 videos per channel with `meta.reduced = true` and a note.
- **Pending on both failure paths.** `claude` missing or too old writes a `pending` row
  (no packet) and an `analyse_competitors` runs row with `status='error'`, then exits 5,
  so the dashboard has something to say; a failed call writes `pending` with the packet
  path. `result_json` of a pending row is `{"error": reason}`.
- **`analyse --summaries --competitors`** runs the stages in that order and skips the
  comparison when the summaries failed (the same `claude` would fail again). `analyse` with
  neither flag is exit 1.
- The `_print_competitor_plan` name was taken by `collect --competitors`; the new dry-run
  printer is `_print_comparison_plan`.
- The 007 whitelist already allows `youtube.com/watch` links; `assert_self_contained` is
  unchanged and the new links pass it.

**Verified.** `pytest -q`: 296 passed (17 in `tests/test_competitor_analysis.py`).
`ruff check .` and `ruff format --check .` clean. Tests prove: packet membership and order;
the newest summary wins; `outlier_ids` gone; the 150 KB reduction to 10 per channel; the
canned analysis validates against the schema; an unknown video id and an unknown channel
id are dropped, counted, and absent from `result_json`; the fake's mechanical `"fake"` ids
are all dropped and the row still stores 5 `next_videos`; the CLI writes one row and one
packet; the dashboard renders 5 `next-video` panels, evidence links with titles, the id
fallback, the pending note with the clock; `--dry-run` calls only `--version` and writes
nothing; empty PATH → pending row + exit 5; bad output → pending row + exit 5; both stages
together; both stages with failing summaries skip the comparison.
`ytscout analyse --competitors --dry-run` and `ytscout dashboard` ran for real.

**Real calls: 6 of 15 allowed.** One was accidental: the old
`test_cli_analyse_competitors_is_still_a_stub` had no `fake_claude` fixture (it expected
exit 2 before any call) and, once the stub was gone, reached the real `claude` from the
test run (it wrote into the test's temp DB, not the real one; 22 s, 34 in / 1,287 out
tokens on an empty packet). Then `analyse --summaries --competitors` on the real DB: 4
summaries (13–15 s each, all from titles, transcripts still `error`) and one comparison
over the own channel's 5 summarised videos: 32 s, `usage` 34 input / 2,185 output tokens
(the input figure excludes cached prompt tokens; Claude Code's `usage` also carries
`cache_read_input_tokens`, which the CLI does not print yet: 031 can chart it). Result:
`confidence = low`, empty `per_competitor` and `topic_gaps` (no approved competitor in the
real DB yet), 3 `our_weaknesses` (head-to-head "versus" Shorts and question hooks below
median; countdown teases and superlative titles above), 5 `next_videos` all citing real own
video ids, 0 ids dropped, and five honest caveats including that the comparison teases are
placeholders to verify. The real dashboard shows 5 next-video panels and 18 watch links.

**Unverified.** The Findings section by eye in a browser (rendered HTML checked by string
only). Behaviour with a real competitor in the packet (none is approved yet): the prompt
and schema handle it, and the tests do, but no real call has.

**For 018 and later.** Approve a competitor (`ytscout serve`) and run `collect
--competitors`, `collect --transcripts`, `analyse --summaries`, `analyse --competitors`
to get the first real per-competitor findings. `run_weekly.ps1` already runs both
`analyse` stages; the comparison exit 5 will log as `ERROR` like the summaries one.
`RunResult.usage` is not stored in the row; if 031 wants tokens per run, add a column or a
`runs` field then.
