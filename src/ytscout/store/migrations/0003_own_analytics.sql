-- 0003: own-channel Analytics (011). own_analytics (from 0001) holds per-video totals per
-- window; own_daily and own_traffic are channel-level. A re-run over the same window or
-- day replaces its row: YouTube revises recent revenue, and the key is the history.
ALTER TABLE own_analytics ADD COLUMN minutes_watched REAL;
ALTER TABLE own_analytics ADD COLUMN likes INTEGER;
ALTER TABLE own_analytics ADD COLUMN collected_at TEXT;

CREATE TABLE own_daily (
    day                 TEXT PRIMARY KEY,
    views               INTEGER,
    revenue_usd         REAL,
    monetized_playbacks INTEGER,
    collected_at        TEXT NOT NULL
);

CREATE TABLE own_traffic (
    window_start        TEXT NOT NULL,
    window_end          TEXT NOT NULL,
    source              TEXT NOT NULL,
    views               INTEGER,
    collected_at        TEXT NOT NULL,
    PRIMARY KEY (window_start, window_end, source)
);
