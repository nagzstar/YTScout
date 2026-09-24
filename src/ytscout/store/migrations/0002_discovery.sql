-- 0002: why `discover` (006) suggested a channel: {score, reasons, hit_count, matched_queries}.
ALTER TABLE channels ADD COLUMN discovery_json TEXT;
