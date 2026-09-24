-- 0004: video_summaries (016) records the schema hash next to the prompt hash, and the
-- transcript status the packet carried, so a summary made from title and description
-- alone can be redone once the transcript arrives.
ALTER TABLE video_summaries ADD COLUMN schema_hash TEXT;
ALTER TABLE video_summaries ADD COLUMN transcript_status TEXT;
