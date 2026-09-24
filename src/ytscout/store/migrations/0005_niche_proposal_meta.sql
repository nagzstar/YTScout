-- 0005: niches (021) carry the brainstorm's extra fields and provenance in one JSON column:
-- why_ai_able, evergreen, faceless_ok, and for LLM rows prompt_hash, schema_hash and the
-- packet path. required_steps_json holds the brainstorm's suspected steps until the step
-- tagger (024) confirms them.
ALTER TABLE niches ADD COLUMN meta_json TEXT;
