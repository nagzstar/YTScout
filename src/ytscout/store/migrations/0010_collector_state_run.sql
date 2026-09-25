-- 0010: collector checkpoints belong to a logical run (032): the ISO week (`2026-W39`) for
-- the weekly collectors, `niche-<id>` for `scout validate`. Nothing wrote the 0001 table,
-- so it is rebuilt rather than altered, to widen the primary key.
DROP TABLE collector_state;
CREATE TABLE collector_state (
    kind    TEXT NOT NULL,
    run_id  TEXT NOT NULL,
    key     TEXT NOT NULL,
    done_at TEXT NOT NULL,
    PRIMARY KEY (kind, run_id, key)
);
