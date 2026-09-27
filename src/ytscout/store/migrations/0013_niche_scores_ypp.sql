-- 0013: months to Partner Programme monetisation per niche x format (054, DESIGN.md 6.6).
-- months_to_ypp is NULL and ypp_reachable is 0 when a good newcomer's rate never fills
-- YouTube's rolling window (or there is no rate to take); ypp_flags_json says which.
-- Rows older than this migration have all three NULL: the dashboard shows them as unknown.
ALTER TABLE niche_scores ADD COLUMN months_to_ypp REAL;
ALTER TABLE niche_scores ADD COLUMN ypp_reachable INTEGER;
ALTER TABLE niche_scores ADD COLUMN ypp_flags_json TEXT;
