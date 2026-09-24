You are proposing YouTube niches for a one-person, faceless, AI-assisted channel
operation. The brief is in a JSON packet file whose absolute path is given on the last
line of this message.

Do this, and nothing else:

1. Read the packet file with the Read tool. Do not browse, do not search, do not open any
   other file, and do not use any other tool.
2. Propose exactly `count` niches (the number in the packet) in the requested JSON output.

A niche is a `format × topic` pair. `format` is `shorts` or `longform`; the packet's
`formats` object says what each means. `topic` is a short slug naming a repeatable
content angle, not a single video: "top5-countdown-space-facts", not "top 5 biggest
planets".

What the packet holds:

- `production_steps`: every step a video needs, its default human hours, and what the
  existing pipeline already automates (`pipeline_coverage`: `automated`, `partial` with
  the remaining human hours in `manual_hours_override`, or `manual`). Steps marked
  `disqualifying_for_faceless` cannot be required.
- `topic_categories`: the fixed list of category ids with descriptions. Every niche must
  use one of them; it decides the revenue-per-view estimate.
- `constraints`: faceless, English, evergreen preferred, both formats wanted, and what the
  production pipeline can and cannot make.
- `existing_niches`: what is already in the database. Do not repeat any of them. Do not
  propose a near-synonym of one either; explore different categories and angles.
- `reference_niche`: the channel's current niche, for the level of effort, not the topic.

Rules for a good proposal:

- **Fit the pipeline.** Research + script + TTS narration + stock/AI visuals + automated
  assembly. If a niche only works with gameplay, screen recording, original filming,
  on-camera testing or a presenter, do not propose it.
- **Spread the categories.** Use at least six different `topic_category` values across
  the set and lean towards the higher-value ones (finance, tech, education, health, DIY,
  cars) when a faceless angle exists there. Do not put more than a quarter of the niches
  into `animals_nature` or `entertainment_pop`.
- **Both formats.** Roughly half `shorts`, half `longform`.
- **Evergreen over news.** Prefer topics that will still be searched in three years.
- **Be specific.** "Personal finance" is a category, not a niche. "Ranked comparisons of
  everyday costs across countries" is a niche.

Field guide:

- `format`: `shorts` or `longform`.
- `topic`: a lowercase slug, letters, digits and single hyphens, under 60 characters,
  unique within your list and not in `existing_niches`.
- `label`: the human name, under 80 characters, e.g. "Top 5 countdowns: engineering
  disasters".
- `topic_category`: one id from `topic_categories`.
- `example_search_queries`: exactly 3 plain-English YouTube searches a viewer of this
  niche would type. They will be run through the YouTube search API to find the channels
  in the niche, so make them the way people search, not keyword lists.
- `why_ai_able`: one or two sentences, under 300 characters, on why the pipeline can make
  these videos with little human time.
- `suspected_manual_steps`: the `id`s of the `production_steps` a human would still have
  to spend meaningful time on for this niche, beyond what the pipeline covers. Only ids
  that appear in the packet. An empty list is allowed. Typical: `visuals_stock` when the
  niche needs specific footage, `research` when facts need careful checking.
- `evergreen`: true when the topic keeps getting views for years.
- `faceless_ok`: true when the niche works with no presenter and no original footage.
  Every proposal should be true; if you are not sure, do not propose it.

Keep every text field short. Do not add fields.
