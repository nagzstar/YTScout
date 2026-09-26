-- 0012: the niche relevance gate (049). A search hit is not proof a channel is in the niche:
-- 027's samples held news outlets and general-interest mega-channels. `score --niches`
-- screens every linked channel on its stored video titles and categories and writes why it
-- was left out here; NULL means the channel counts. The row stays so the dashboard can show
-- what was excluded, and a re-score with new thresholds can bring it back.
ALTER TABLE niche_channels ADD COLUMN excluded_reason TEXT;
