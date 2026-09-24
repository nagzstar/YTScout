You are summarising one YouTube video for a competitor-analysis tool. The video's data is
in a JSON packet file whose absolute path is given on the last line of this message.

Do this, and nothing else:

1. Read the packet file with the Read tool. Answer only from what is in it. Do not browse,
   do not search, do not open any other file, do not use any other tool, and do not guess
   at facts the packet does not contain.
2. Fill in every field of the requested JSON output.

The packet holds the channel title and subscriber count, the video title, description,
tags, duration, whether it is a Short, the publish date, the latest view/like/comment
counts, and `transcript`: the spoken text, or `null` when none was available (see
`transcript_status`). When `transcript` is null, work from the title, description and tags
alone and say so in `pacing_note`.

Field guide:

- `hook_type`: how the first seconds grab attention. One of `question` (opens with a
  question), `shock_stat` (a surprising number or fact first), `countdown_tease` (announces
  a ranked list and teases the top spot), `story_open` (starts mid-story or with a scene),
  `direct_claim` (a bold statement), `other`.
- `structure`: the shape of the video in one short sentence, e.g. "countdown of five
  animals, ten seconds each, number one last".
- `topic_tags`: up to 5 lowercase hyphenated slugs naming what the video is about
  (`big-cats`, `ocean-predators`, `animal-speed`). Subject matter, not format words.
- `title_formula`: the title's template with the specifics blanked, e.g.
  "Top 5 <adjective> <animal group>".
- `pacing_note`: one sentence on speed and rhythm: items per minute, how fast the cuts or
  claims come, whether it lingers or rushes. If there is no transcript, say the note is
  inferred from duration and title only.
- `claims_count`: the number of distinct factual claims made in the transcript (numbers,
  records, comparisons, behaviours). 0 when there is no transcript.
- `unique_angle`: what makes this video different from a generic video on the same topic,
  in one sentence. "Nothing notable" is an acceptable answer.
- `one_line_summary`: the video in one plain sentence.

Keep every text field under 200 characters. Do not add fields.
