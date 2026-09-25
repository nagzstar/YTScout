-- 0006: when `scout validate` (022) last sampled a niche. Lifecycle:
-- proposed -> validated -> scored -> tracking | shelved (DESIGN.md §5.4).
ALTER TABLE niches ADD COLUMN validated_at TEXT;
