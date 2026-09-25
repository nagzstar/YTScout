-- 0009: each `runs` row (031) records what it spent: ledger units, Claude calls and the
-- token counts and cost estimate `claude -p --output-format json` reported (NULL when
-- no call reported the field), and the tail of the traceback when it raised.
ALTER TABLE runs ADD COLUMN units_used INTEGER;
ALTER TABLE runs ADD COLUMN claude_calls INTEGER;
ALTER TABLE runs ADD COLUMN claude_input_tokens INTEGER;
ALTER TABLE runs ADD COLUMN claude_output_tokens INTEGER;
ALTER TABLE runs ADD COLUMN claude_cost_usd_est REAL;
ALTER TABLE runs ADD COLUMN error_tail TEXT;
