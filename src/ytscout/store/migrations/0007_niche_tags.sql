-- 0007: the step tagger (024). required_steps_json is overwritten with the tagged steps;
-- tag_prompt_hash says which prompt produced them (NULL = only the brainstorm's guess, so
-- `score --niches` skips the niche). niche_scores.flags already holds uncalibrated etc.
ALTER TABLE niches ADD COLUMN needs_specific_footage INTEGER
    CHECK (needs_specific_footage IN (0, 1));
ALTER TABLE niches ADD COLUMN tag_prompt_hash TEXT;
ALTER TABLE niches ADD COLUMN tag_schema_hash TEXT;
ALTER TABLE niches ADD COLUMN tag_notes TEXT;
ALTER TABLE niches ADD COLUMN tagged_at TEXT;
