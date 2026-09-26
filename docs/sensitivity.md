# Sensitivity check: do the thresholds change the ranking?

Generated 2026-09-26T23:51:46Z by `ytscout scout sensitivity` (issue 028) on 3 niche(s), 27 settings. Re-run it for current numbers. Nothing here is written to the DB and `config/scoring.yaml` is unchanged: changing a default is Nagz's call.

The grid is every combination of `outlier_multiplier` ∈ {2, 3, 4}, `small_subs_max` ∈ {5000, 10000, 20000}, `small_age_days` ∈ {180, 365, 540}. The default is `outlier_multiplier=3 small_subs_max=10000 small_age_days=365`.

## What can move

`score` is £/month ÷ manual hours/month (DESIGN.md §6.5) and `opportunity` (§6.1) does not enter it. So `outlier_multiplier` moves the opportunity column only and can never change a rank. `small_subs_max` and `small_age_days` decide which channels are small, which moves both the opportunity and the newcomer views (§6.2) that the £/month, and so the score and the rank, are built on. Manual hours do not depend on any grid axis.

## Per niche: score, opportunity and rank across the grid

`rank` is the rank under the default; `range` the lowest and highest rank in the grid; `stable` how many of the 27 settings keep the default rank. Ranks are over every niche in the run, both formats together, as `score --niches` prints them; the dashboard ranks each format on its own.

| id | label | format | score min | default | max | opp min | default | max | rank | range | stable |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | One-animal deep dives (8-12 min) | longform | 10.863 | 14.740 | 129.258 | 0.331 | 0.345 | 0.439 | 1 | 1-1 | 27/27 |
| 33 | Aviation incidents explained: what went wrong an | longform | 3.875 | 10.207 | 10.207 | 0.196 | 0.232 | 0.250 | 2 | 2-2 | 27/27 |
| 1 | Top 5 countdowns: dangerous animals | shorts | 0.009 | 0.009 | 0.011 | 0.105 | 0.110 | 0.147 | 3 | 3-3 | 27/27 |

## Reading

- `outlier_multiplier` alone never changes the ranking (2 settings tried).
- `small_subs_max` alone never changes the ranking (2 settings tried).
- `small_age_days` alone never changes the ranking (2 settings tried).
- No setting changes the top-3: the default ranking holds in all 27.
- Every niche keeps its default rank in all 27 settings.
- Widest score swing: #3 One-animal deep dives (8-12 min) spans 10.863-129.258 (11.9x), lowest at `outlier_multiplier=2 small_subs_max=5000 small_age_days=540`, highest at `outlier_multiplier=2 small_subs_max=20000 small_age_days=180`.

## Every setting

One row per grid cell; `*` marks the default.

| outlier_multiplier | small_subs_max | small_age_days | #1 rank | #3 rank | #33 rank | #1 score | #3 score | #33 score |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 5000 | 180 | 3 | 1 | 2 | 0.009 | 16.399 | 3.875 |
| 2 | 5000 | 365 | 3 | 1 | 2 | 0.009 | 14.740 | 10.207 |
| 2 | 5000 | 540 | 3 | 1 | 2 | 0.009 | 10.863 | 10.207 |
| 2 | 10000 | 180 | 3 | 1 | 2 | 0.009 | 16.399 | 3.875 |
| 2 | 10000 | 365 | 3 | 1 | 2 | 0.009 | 14.740 | 10.207 |
| 2 | 10000 | 540 | 3 | 1 | 2 | 0.009 | 10.863 | 10.207 |
| 2 | 20000 | 180 | 3 | 1 | 2 | 0.010 | 129.258 | 3.875 |
| 2 | 20000 | 365 | 3 | 1 | 2 | 0.010 | 19.891 | 10.207 |
| 2 | 20000 | 540 | 3 | 1 | 2 | 0.011 | 15.846 | 10.207 |
| 3 | 5000 | 180 | 3 | 1 | 2 | 0.009 | 16.399 | 3.875 |
| 3 | 5000 | 365 | 3 | 1 | 2 | 0.009 | 14.740 | 10.207 |
| 3 | 5000 | 540 | 3 | 1 | 2 | 0.009 | 10.863 | 10.207 |
| 3 | 10000 | 180 | 3 | 1 | 2 | 0.009 | 16.399 | 3.875 |
| 3 | 10000 | 365 * | 3 | 1 | 2 | 0.009 | 14.740 | 10.207 |
| 3 | 10000 | 540 | 3 | 1 | 2 | 0.009 | 10.863 | 10.207 |
| 3 | 20000 | 180 | 3 | 1 | 2 | 0.010 | 129.258 | 3.875 |
| 3 | 20000 | 365 | 3 | 1 | 2 | 0.010 | 19.891 | 10.207 |
| 3 | 20000 | 540 | 3 | 1 | 2 | 0.011 | 15.846 | 10.207 |
| 4 | 5000 | 180 | 3 | 1 | 2 | 0.009 | 16.399 | 3.875 |
| 4 | 5000 | 365 | 3 | 1 | 2 | 0.009 | 14.740 | 10.207 |
| 4 | 5000 | 540 | 3 | 1 | 2 | 0.009 | 10.863 | 10.207 |
| 4 | 10000 | 180 | 3 | 1 | 2 | 0.009 | 16.399 | 3.875 |
| 4 | 10000 | 365 | 3 | 1 | 2 | 0.009 | 14.740 | 10.207 |
| 4 | 10000 | 540 | 3 | 1 | 2 | 0.009 | 10.863 | 10.207 |
| 4 | 20000 | 180 | 3 | 1 | 2 | 0.010 | 129.258 | 3.875 |
| 4 | 20000 | 365 | 3 | 1 | 2 | 0.010 | 19.891 | 10.207 |
| 4 | 20000 | 540 | 3 | 1 | 2 | 0.011 | 15.846 | 10.207 |
