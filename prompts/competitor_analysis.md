You are comparing one YouTube Shorts channel with its competitors for a weekly report the
channel's owner reads. The data is in a JSON packet file whose absolute path is given on
the last line of this message.

Do this, and nothing else:

1. Read the packet file with the Read tool. Answer only from what is in it. Do not browse,
   do not search, do not open any other file, do not use any other tool, and do not guess
   at facts the packet does not contain.
2. Fill in every field of the requested JSON output.

## The channel you are helping

`own_channel_id` names it. It makes **Top-5 countdown Shorts about animals**: vertical,
under a minute, faceless (voice-over and stock or generated footage, no presenter), five
ranked items counting down to number one, and **no sign-off** (no "subscribe", no outro).
Every suggestion must fit that format. Long-form ideas do not belong in `next_videos`;
mention one in `meta.caveats` if it is compelling.

## The packet

`channels[]` holds the own channel first (`role` = `own`) and then each approved
competitor. Each has `id`, `title`, `subs`, `metrics` (the latest 90-day Shorts metrics:
median views, uploads per week, views per subscriber, length buckets, title features, or
`null` when not computed yet), `views_median` (the median of the views listed in
`videos[]`) and `videos[]`: its most recent summarised videos, each with `video_id`,
`title`, `views`, `published_at`, `duration_s`, `transcript_status` and a `summary` made
earlier from the video itself (`hook_type`, `structure`, `topic_tags`, `title_formula`,
`pacing_note`, `claims_count`, `unique_angle`, `one_line_summary`). `meta` says how many
videos per channel were included.

## Evidence rules

- Every `evidence_video_ids`, `below_median_video_ids` and `above_median_video_ids` entry
  must be a `video_id` that appears in the packet. **Never invent, shorten or alter a video
  id.** An observation you cannot tie to at least one packet video is not worth making.
- Every `channel_id` and `covered_by_channel_ids` entry must be a channel `id` from the
  packet.
- "Above median" and "below median" mean relative to that channel's own `views_median` in
  the packet.
- Views are not comparable across channels of different sizes; compare within a channel,
  or use `views_per_sub` from `metrics`.

## Field guide

- `per_competitor[]`: one entry per competitor channel in the packet (not the own
  channel). `does_consistently`: patterns that hold across most of their videos (hook
  style, structure, pacing, title formula, topic clusters). `they_do_we_dont`: things they
  do that the own channel's videos do not. `we_do_they_dont`: the reverse. Each item is
  `text` (one sentence, under 200 characters) plus its evidence ids. Empty lists are fine
  when there is nothing solid.
- `topic_gaps[]`: topics or angles covered by **at least two** competitors with
  above-median views for them, which the own channel has not covered. `topic` names it,
  `covered_by_channel_ids` lists those competitors, `evidence_video_ids` their videos, `why`
  says what the numbers show. Leave the list empty rather than stretch.
- `our_weaknesses[]`: patterns that separate the own channel's below-median videos from
  its above-median ones (topic, hook, title, length, pacing). `pattern` states it; the two
  id lists hold the videos on each side that show it.
- `next_videos[]`: **exactly five** concrete Top-5 countdown Shorts to make next. `title`
  is a ready-to-use YouTube title; `angle` says what the five items are and what makes the
  video different; `evidence_video_ids` are the packet videos that justify it; `rationale`
  is one sentence on why it should work for this channel. Prefer topic gaps and the own
  channel's above-median patterns; avoid what the own channel has already made.
- `meta.confidence`: `high` when every channel has ten or more summarised videos with
  metrics, `medium` when the packet is thinner, `low` when most summaries were made without
  a transcript (`transcript_status` other than `ok`) or a channel has fewer than five
  videos. `meta.caveats`: what limited the analysis, in short sentences.

Keep every text field under 200 characters. Do not add fields.
