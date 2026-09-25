# 018 — First real end-to-end weekly run and review

**Type**: Active
**Blocked by**: 012, 013, 017
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: scripts/run_weekly.ps1, dashboard/index.html, logs/, issues/ (new ones)
**Milestone**: M2

## Why

Everything in M1–M2 exists. This is the first time it runs as one thing and the first
time the Findings are read by the person they are for. What is wrong becomes issues.

## Scope

1. ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
   ```
   (or `Start-ScheduledTask 'YTScout Weekly'` if 014 is done).
2. Read `logs\weekly-*.log`: every step's exit code, total units, wall time.
3. Open the dashboard. Read the Findings as if a consultant wrote them:
   - Are the 5 suggestions ones you would actually make? Which not, and why?
   - Do the topic gaps hold up when you open the evidence links?
   - Is anything in the metrics table obviously wrong (a channel with 0 uploads/week that
     posts daily, a median that can't be right)?
4. Write **new issues** (033 onward) for each concrete problem, AFK where the fix is
   clear, Active where you need to decide something. Keep them small.

## Out of scope

- Fixing anything in this issue.

## Acceptance criteria

- [x] The run completed with `score` and `dashboard` exiting 0, whatever the collectors did.
- [x] Outcome: units spent, wall time, Claude calls made (from `runs`), one line per
      suggestion saying keep / drop / why, and the list of new issue numbers created.

## Notes

Expect the first Findings to be mediocre. The point of this issue is the list of what to
fix, not a verdict on the tool.

## Outcome (closed 2026-09-25)

**The run.** `scripts\run_weekly.ps1` by hand at 13:54 UK (the Task Scheduler job is
040's to re-register). Log: `logs/weekly-20260925-1354.log`. Every step exited 0, `DONE
exit 0`. A Task Scheduler run of the same script at 01:04 the same morning (issues 038 and
039 were working from it) is the comparison point below.

| Step | Exit | Wall | Data API units | Claude calls | Notes |
|---|---|---|---|---|---|
| collect --own | 0 | 1 s | 3 | | 9 videos, one new |
| collect --competitors | 0 | 5 s | 23 | | 8 channels, 341 videos |
| collect --analytics | 0 | 1 s | 0 | | impressions/CTR still null (API rejects them; known from 011) |
| collect --transcripts | 0 | **37 m 42 s** | 0 | | **ok 0, error 100**, all `SSLError` |
| collect --niches | 0 | 0 s | 0 | | no tracked niches |
| analyse --summaries | 0 | 9 m 36 s | | 40 | 40 of 40 candidates, 3.61M in / 22k out tokens (input includes cache reads) |
| analyse --competitors | 0 | 3 m 2 s | | 1 | row 3, 8 channels, 70 videos, 66 in / 12,807 out, 0 ids dropped |
| score --all | 0 | 0 s | | | 32 channel_metrics rows |
| dashboard | 0 | 0 s | | | 327,611 bytes, 58 watch links |
| **Total** | **0** | **50 m 30 s** | **26** (ledger 2026-09-25 = 26) | **41** | |

The `runs` rows 29–37 carry `log_path`, `units_used` and `claude_calls` for every step
(031 works). Three quarters of the wall time is the transcripts step failing every video
after the full 21 s backoff; see 041.

**Root cause of the transcript failure, found during the run.** A one-off
`transcripts.fetch(id, backoff=())` returns `SSLError`: `CERTIFICATE_VERIFY_FAILED, unable
to get local issuer certificate` for `www.youtube.com`. Issue 033's CA bundle reaches
httplib2 and the OAuth session but not `youtube-transcript-api`'s own `requests.Session`.
The 01:04 run *did* fetch 32 transcripts before being IP-blocked, so the interception is
intermittent; either way the library must verify against the bundle. 039's breaker did
not trip because the failure is not `RequestBlocked`.

**The Findings, read as a consultant's memo** (`competitor_analyses.id 3`, confidence
`low`, 7 caveats). Far better than row 2 from 01:04, which had null metrics and judged
AstroFact from one statues video. Evidence ids were resolved to titles and views by hand.

Suggestions:
1. *Top 5 Animal Defences You Should Never Trigger* — **keep**. Beast tier 898k (5x
   median) and CritterClipzLOL 472k (12x) on the exact topic; nothing of ours covers it.
2. *Top 5 Toughest Animals Alive* — **keep**. Beast tier 494k (3x median) plus 142k on
   "living armor"; reuses our versus research in the countdown format that works for us.
3. *Top 5 Animals That Die to Give Birth* — **keep**. Beast tier 1.96M (12x); the coco
   scene evidence (8k–25k) is above that channel's median but adds little.
4. *Top 5 Ways Humans Accidentally Kill Wildlife* — **keep, strongest of the five**.
   Beast tier 3.0M and a 582k Part 2 on human mistakes; a conservation-guilt frame we
   have never tried.
5. *Top 5 Deadliest Animals in Australia* — **drop as written**. Evidence is our own best
   video (1,403), our hours-old newest (133) and an AstroFact Short *below* its median
   (13.7k vs 57.8k). It is a sequel of our own hit, not a competitor-backed gap; the
   prompt lets that through. See 048.

Topic gaps: both hold up. *Defence mechanisms* (Beast tier 898k, CritterClipzLOL 472k)
and *grim animal fates* (Beast tier 1.96M; coco scene's three tragic-fate uploads all above
its 8.4k median). Every evidence link opened to the video it claimed.

Our weaknesses: the three patterns are the same fact three ways (versus videos with
question titles below median, Top 5 countdowns with a bracketed shock stat above), which
on 9 videos is as much as can be said. Plausible and actionable, but the schema asks for
three and gets one padded to three.

Per-competitor cards: Beast tier, CritterClipzLOL, LOWLIGHTS and AstroFact are useful and
specific (title grammar, seconds per item, where the like/subscribe ask sits, "picks
obscure species over the obvious"). Curious Bone's card describes 6–10 minute essays
because every one of its packet videos is long-form (packet durations 381–574 s) inside
a Shorts comparison. Woofy D. Luffy's card says the channel is "mostly not about animals";
coco scene's says clip compilations with "no structure or facts to hold onto". Those two
should not be `approved`.

**Metrics table.** Nothing impossible. Uploads/week reconcile with 90-day video counts
(Beast tier 6.92 = 89/12.9, LOWLIGHTS 0.62 = 8/12.9, own 0.70 = 9/12.9). Medians match
the snapshot views. Two soft problems: our newest video (133 views, hours old) is inside
the median and the packet's below-median list (046), and views/sub for a 32-subscriber
channel (34.4) is a number no one should read next to 0.04–1.0 for the competitors.

**Dashboard.** Not judged by eye in a browser in this session (no browser tool). Checked
by string: the Findings section renders row 3 (`run_at 2026-09-25T13:44:42Z`), 58
`youtube.com/watch` links, no "analysis pending" note. Two `ytscout serve` processes
from 2026-09-24 13:13 are still listening on port 8765, so the page is open at
`http://localhost:8765` for Nagz to judge.

**Other observations, not filed.** `analyse --summaries` reports 3.6M input tokens for
40 calls: each `claude -p` carries the full Claude Code system prompt as cache reads, so
the Runs panel's token column is dominated by cache, not by packets. Analytics
impressions/CTR are still rejected by the API (011's retry-without path is doing its job).
`collect --niches` and `score --niches` have nothing to do until 027 tracks a niche.

**New issues filed (041–048):**
- 041 AFK — transcripts verify TLS against the 033 CA bundle, and a systemic
  `SSLError`/`ConnectionError` streak trips the breaker instead of costing 21 s a video.
- 042 AFK — transcript per-video lines reach the log as they happen (today: nothing for
  37 minutes, then 100 lines).
- 043 AFK — `score --competitors` runs before `analyse` so the packet carries this week's
  metrics, not last week's (or none).
- 044 AFK — summaries pick each channel's outliers first and share the 40 across
  channels, instead of the newest 40 overall (coco scene and LOWLIGHTS had 0 summaries
  after two runs; the 17M and 5.9M hits were not in the packet).
- 045 AFK — the packet is Shorts-only; long-form videos go to a later long-form packet.
- 046 AFK — videos younger than 7 days sit out medians, outlier baselines and the
  above/below-median lists.
- 047 Active — Nagz decides whether Woofy D. Luffy, coco scene and Curious Bone stay
  `approved` (recommendation: rejected, watch, watch).
- 048 AFK — a suggestion or topic gap must cite a competitor video above its channel
  median or be tagged weak evidence.

Order that pays back fastest: 041 (37 minutes a week and every summary is titles-only
until it lands), then 047 and 044 together (they decide what the 40 summaries a week are
spent on), then 043, 045, 046, 048, 042.
