You are costing YouTube niches for a one-person, faceless, AI-assisted channel operation.
For each niche you say which production steps a video in that niche needs. The brief is
in a JSON packet file whose absolute path is given on the last line of this message.

Do this, and nothing else:

1. Read the packet file with the Read tool. Do not browse, do not search, do not open any
   other file, and do not use any other tool.
2. Return one entry per niche in the packet's `niches` list, in the requested JSON
   output, with the same `niche_id`. Do not skip a niche and do not invent one.

What the packet holds:

- `production_steps`: the fixed list of steps a video can need, each with an `id`, a
  `label`, default human hours and notes. `visuals_stock` has `specific_footage_hours`:
  the cost when generic stock or AI images will not do.
- `niches`: up to 10 niches. Each has `niche_id`, `label`, `format` (`shorts` or
  `longform`), `topic_category`, the `queries` used to find its channels, `why_ai_able`
  (the brainstorm's reason it suits the pipeline, may be empty) and `sample_titles`:
  real titles of popular videos from channels already making this niche. The titles are
  the best evidence of what a video in the niche actually is.

For each niche:

- `required_steps`: the `id`s of every step a video in this niche needs, whoever does it.
  Most niches need `research`, `script`, `voiceover`, `visuals_stock`, `assembly`,
  `metadata`, `upload` and `qa`. Add `thumbnail` for `longform` (Shorts have none, but
  listing it costs nothing). Add `community` only when the niche lives on comment replies.
  Leave out a step the niche genuinely does not need; do not leave out a step because a
  pipeline might automate it — automation is costed separately.
- **`footage_original` and `presenter` disqualify a niche for a faceless pipeline.**
  Include `footage_original` only when the niche truly cannot work without original
  filming, gameplay capture or screen recording (e.g. game walkthroughs, software
  tutorials, product unboxings). Include `presenter` only when a face on camera is the
  point (e.g. reaction videos, vlogs). If stock clips, AI images, maps, charts or archive
  material with narration would make a watchable video, do not include either.
- `needs_specific_footage`: true when generic stock or AI visuals will not do and each
  video needs footage of specific real things (a named car model, a real event, a
  particular building, a specific species behaving a specific way). False when
  illustrative stock or generated images carry the video.
- `notes`: one sentence, under 300 characters, saying what drove the choice, especially
  any disqualifying step or specific-footage call.

Keep every text field short. Do not add fields.
