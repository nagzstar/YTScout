# 017 — `analyse --competitors`: the comparison call and the Findings section

**Type**: AFK
**Blocked by**: 016
**Add dirs**: none
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
