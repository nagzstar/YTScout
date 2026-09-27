-- 0014: reused-content monetisation risk per niche (055, DESIGN.md decision 18, 6.4).
-- The step tagger rates the pipeline's output in the niche against YouTube's reused-content
-- policy: low | medium | high, with a one-sentence reason. NULL = tagged before this
-- migration (or never); the tagger re-tags such niches because the prompt hash changed.
ALTER TABLE niches ADD COLUMN reused_content_risk TEXT
    CHECK (reused_content_risk IN ('low', 'medium', 'high'));
ALTER TABLE niches ADD COLUMN reused_content_reason TEXT;
