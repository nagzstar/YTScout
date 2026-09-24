# Pipeline audit: what `top-five-animals-1` already does

Audited 2026-09-24 against pipeline commit `67b4c1d`, read-only (issue 019). The result
is `config/pipeline_coverage.yaml`; this page is the reasoning. Nagz corrects both in
issue 020. Re-run `.venv\Scripts\python.exe -m ytscout audit` for the current numbers.

Paths below are relative to `C:\Users\nagaj\git\top-five-animals-1`.

## The short version

For the video the pipeline already makes, a human spends about **1.2 hours per Short**
and **1.25 hours per long-form deep dive** out of the 11.25 hours the DESIGN.md §6.4
defaults would cost by hand (3.25 h without the two disqualifying steps). Almost half of
what remains is picking footage, because Storyblocks cannot be automated. Everything else
is reading and approving what Claude drafted, plus the one watch-through before publish.

| step | default h | coverage | Short h | long-form h |
|------|----------:|----------|--------:|------------:|
| research | 0.75 | partial | 0.20 | 0.20 |
| script | 0.50 | partial | 0.10 | 0.10 |
| voiceover | 0.25 | partial | 0.05 | 0.05 |
| visuals_stock | 1.00 | partial | 0.50 | 0.50 |
| footage_original (disqualifying) | 3.00 | manual | 3.00 | 3.00 |
| presenter (disqualifying) | 4.00 | manual | 4.00 | 4.00 |
| assembly | 1.00 | automated | 0.00 | 0.00 |
| thumbnail | 0.25 | partial | 0.00 | 0.05 |
| metadata | 0.15 | partial | 0.05 | 0.05 |
| upload | 0.10 | partial | 0.05 | 0.05 |
| qa | 0.15 | manual | 0.15 | 0.15 |
| community | 0.10 | manual | 0.10 | 0.10 |
| **manual_hours_per_video** (excluding disqualifying steps) | | | **1.20** | **1.25** |

At the settings defaults (20 Shorts and 4 long-form videos a month) that is 24 h/month
for Shorts and 5 h/month for long-form. The two disqualifying steps are shown for
completeness: a niche that needs either does not fit this pipeline at all.

How the arithmetic works: `automated` costs 0, `manual` costs the step's default,
`partial` costs the audited override. A Short's thumbnail is 0 whatever the coverage says
(`shorts_hours`), and QA never drops below 0.1 h (`floor_hours`) because a human should
always look. A format the pipeline could not produce would be costed as fully manual;
both formats are supported, see below.

## Stage by stage

### Topic research and fact-check: partial, 0.2 h

- Evidence: `.claude/skills/research/SKILL.md`, `.claude/skills/topics/SKILL.md`,
  `scripts/new_item.py`.
- The research skill has Claude find one reputable source and one number per entry with
  WebSearch and WebFetch, rank by the sourced numbers and write `research.md` with a
  caveat line. `check_script.py` later proves every spoken number came from that file.
- Where the human is: the skill's "Human gate" presents the entry table and asks for
  approval before the status becomes `researched`. A backlog row becomes a video only
  when a person marks it `PROMOTE` (`new_item.py` refuses otherwise). When footage is in
  doubt, the topics skill asks the user to search Storyblocks by hand, since an agent
  may never crawl it.
- Not automated: nothing. The 0.2 h is reading, approving, and the occasional
  Storyblocks check.

### Script: partial, 0.1 h

- Evidence: `.claude/skills/script/SKILL.md`, `scripts/check_script.py`.
- Claude writes the annotated script, three hook variants and the TTS-ready VOICEOVER
  block. `check_script.py` reads the spoken numbers back out as values and matches them
  to `research.md`, refuses any numeral in the voiceover block, and projects the
  duration at the channel's measured narration rate.
- Where the human is: one read and an approve or a reject-with-note.

### Voiceover: partial, 0.05 h

- Evidence: `scripts/gen_vo.py`, `.claude/skills/produce/SKILL.md`.
- `gen_vo.py` calls ElevenLabs with timestamps, applies the pronunciation overrides from
  `production.md`, and writes `vo.mp3` plus the word timings every caption and shot start
  is cut from. Long-form gets one take per segment.
- Where the human is: the produce skill asks the user to listen to `vo.mp3` before
  moving on. A mangled species name means one override and a re-run.

### Visual sourcing: partial, 0.5 h. The biggest remaining human step.

- Evidence: `scripts/storyblocks.py`, `scripts/fetch_footage.py`,
  `scripts/footage_lib.py`, `scripts/gen_images.py`, `scripts/gen_clips.py`,
  `scripts/contact_sheet.py`, `.claude/skills/produce/SKILL.md`.
- The primary library is Storyblocks. It has no API and its terms forbid bots, so
  `storyblocks.py` only writes a wanted-list with search links; the owner searches and
  downloads each clip and `--pick-up` files what came down. That is real, per-video,
  per-clip human time and it does not go away without a different library.
- The free fallback (`fetch_footage.py`, Pexels then Pixabay) auto-picks the sharpest
  match per search term, but its own docstring says a person checks each clip before the
  video is approved. `footage_lib.py` reuses clips across videos, on the owner's by-eye
  verdicts, so this cost falls as the library fills.
- AI stills are generated locally for free (`gen_images.py`); AI motion goes through
  Replicate (`gen_clips.py`) under a cost cap only a person may override, and the user
  watches the clips and rejects or regenerates per shot. `contact_sheet.py` renders one
  frame per shot for that review.
- 0.5 h of the 1.0 h default is a conservative middle: five clips from Storyblocks plus a
  contact-sheet pass. Nagz knows the true figure; adjust in 020.

### Screen recording / gameplay / original footage: manual, no evidence

- No evidence, assumed manual. Nothing in `scripts/` records a screen, captures gameplay
  or handles original footage; the pipeline is stock-and-AI only. Disqualifying for a
  faceless pipeline and excluded from the totals.

### Face-cam / presenter: manual, no evidence

- No evidence, assumed manual. The channel is faceless by design (`README.md`) and
  nothing films or edits a presenter. Disqualifying and excluded from the totals.

### Assembly and edit: automated

- Evidence: `scripts/assemble.py`, `scripts/assemble_long.py`, `scripts/captions.py`,
  `scripts/make_cards.py`, `scripts/music.py`, `scripts/fetch_sfx.py`,
  `scripts/versus_edit.py`.
- `assemble.py` renders the 1080x1920 Short in one ffmpeg pass from `production.md`:
  clips and stills, word-timed burned-in captions, stat cards, the music bed, animal
  sound effects and the voiceover. `versus_edit.py` builds the versus timeline.
  `assemble_long.py` renders the 16:9 deep dive segment by segment and `--splice` joins
  the approved segments into `final.mp4`.
- Where the human is: nowhere in the render itself. Watching the result is counted under
  QA. The long-form workflow has the owner review each segment before splicing; that
  review time is not in this file yet (see "What this undercounts").

### Thumbnail: partial, 0.05 h (long-form only)

- Evidence: `scripts/make_thumbnail.py`, `.claude/skills/package/SKILL.md`,
  `.claude/skills/publish/SKILL.md`.
- `make_thumbnail.py` draws both `thumbnail.jpg` (1280x720) and `thumbnail-short.jpg`
  (1080x1920) from the package's Upload block with Pillow and ffmpeg. No AI, no cost.
- Shorts do not need one: the package skill says a custom thumbnail is optional and the
  first frame is what matters, so a Short costs 0 here. Long-form requires one and the
  package skill has the human approve the composition: 0.05 h.

### Title, description, tags: partial, 0.05 h

- Evidence: `.claude/skills/package/SKILL.md`, `scripts/yt_upload.py`.
- Claude writes five title variants, description, tags, hashtags, playlist, the
  pinned-comment text and the Upload YAML block into `package.md`. Title rules are
  checked by pytest and `yt_upload.py --dry-run` validates the block.
- Where the human is: pick a title, approve.

### Upload and schedule: partial, 0.05 h

- Evidence: `scripts/yt_upload.py`, `scripts/approve_week.py`,
  `.claude/skills/publish/SKILL.md`.
- `yt_upload.py` uploads and schedules through the YouTube Data API from `package.md`,
  after `channel_guard` confirms the token is the channel's and a consent gate reads the
  ticked "Publish consent" box and slot in the content item.
- Where the human is: only `approve_week.py`, run by the owner at an interactive terminal
  with one keypress per item, ticks that box. It refuses to run without a terminal, so
  no unattended run can publish. 0.05 h is that keypress and the calendar slot,
  amortised per video.

### QA before publish: manual, 0.15 h

- Evidence: `scripts/check_script.py`, `scripts/check_render.py`,
  `.claude/skills/produce/SKILL.md`, `.claude/skills/publish/SKILL.md`.
- Automated checks exist and are good: `check_render.py` reads the rendered file with
  ffprobe and checks the canvas, the three-minute limit, that captions are really burned
  in, that the last frame matches the first for the loop, that the audio runs to the
  end, and the loudness target.
- They add gates; they do not remove the human one. The produce skill asks the user to
  watch `final.mp4` end to end (right animal on every entry, captions in sync, nothing
  graphic, music under the voice, each animal sound the right species) and the publish
  skill runs a pre-flight checklist before every upload. The human still spends the
  full default, so this is `manual`, and it never goes below 0.1 h by design.

### Community and comments: manual, no evidence

- No script reads, replies to or moderates comments; `yt_analytics.py` only counts them.
  The publish skill has the pinned comment posted by hand ("the API does not pin"), and
  the channel skill triages comments only when the user pastes them in. Manual.

## What the pipeline cannot do

- **Long-form: partly.** `channel/style-guide.md` opened long-form on 2026-09-22 with
  three kinds. The one-animal deep dive (pipeline issue 041) is built end to end:
  research, script, package and production sections in the skills, templates, a 16:9
  assembler and thumbnail. Compilations of published Shorts (040) and versus tournaments
  (042) are "a brief and nothing more". The analytics and weekly skills are still
  Shorts-only, which affects retros, not production hours. So `formats_supported` is
  `[shorts, longform]`, with long-form meaning "a deep dive".
- **Thumbnails: yes**, for both formats, drawn from a template. No AI thumbnail art.
- **Original footage and presenters: no**, and nothing is planned. Any niche that needs
  either is out.
- **Specific real footage: only what Storyblocks, Pexels and Pixabay hold.** A niche that
  needs footage of a particular event, product or place is costed at 2.5 h
  (`specific_footage_hours`) and the pipeline saves nothing on it.
- **Comments: no.**
- **Other platforms: parked.** `crosspost.py` and the TikTok, Instagram and Facebook
  uploaders exist but refuse to run while the style guide's `[platforms]` block has them
  off.

## What this undercounts, for Nagz to correct in 020

- **Long-form QA.** Watching an 8-15 minute video, and reviewing each segment before
  the splice, is more than 0.15 h. A single coverage value per step cannot say "0.15 for
  a Short, 0.5 for long-form"; raise `qa` if long-form matters and issue 023 can add a
  per-format override then.
- **Visual sourcing** is a guess at 0.5 h. It is the number that moves the total most.
- **Storyblocks cost.** The subscription is money, not hours, and is not in this model.

## Method

Read, never written: `README.md`, `CLAUDE.md`, `scripts/README.md`, every `scripts/*.py`
listed above, every `.claude/skills/*/SKILL.md`, `channel/style-guide.md` and
the `templates/` folder. Not read: `.env`, `scripts/.secrets/`.
`git -C ... status --porcelain` before and after the session shows the same single
pre-existing modification (`videos/INDEX.md`), which this session did not touch.
