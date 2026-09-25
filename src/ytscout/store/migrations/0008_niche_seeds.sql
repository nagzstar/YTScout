-- 0008: `scout snowball` (026) stores the channel ids a proposed niche was clustered from,
-- as a JSON list, so `scout validate` can start from them.
ALTER TABLE niches ADD COLUMN seed_json TEXT;
