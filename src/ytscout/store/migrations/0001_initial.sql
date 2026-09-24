-- 0001: initial schema (DESIGN.md §10 plus the 002 additions).
-- Timestamps are ISO-8601 UTC text: YYYY-MM-DDTHH:MM:SSZ.
-- *_snapshots tables are append-only; triggers at the end refuse UPDATE and DELETE.

CREATE TABLE channels (
    id                  TEXT PRIMARY KEY,
    title               TEXT,
    custom_url          TEXT,
    country             TEXT,
    created_at          TEXT,
    uploads_playlist_id TEXT,
    role                TEXT NOT NULL CHECK (role IN ('own', 'competitor', 'niche_sample')),
    status              TEXT CHECK (status IN ('approved', 'rejected', 'watch')),
    first_seen          TEXT NOT NULL,
    last_refreshed      TEXT
);

CREATE TABLE channel_snapshots (
    id          INTEGER PRIMARY KEY,
    channel_id  TEXT NOT NULL REFERENCES channels(id),
    captured_at TEXT NOT NULL,
    subs        INTEGER,
    view_count  INTEGER,
    video_count INTEGER
);
CREATE INDEX idx_channel_snapshots_channel_captured
    ON channel_snapshots(channel_id, captured_at);

CREATE TABLE videos (
    id           TEXT PRIMARY KEY,
    channel_id   TEXT NOT NULL REFERENCES channels(id),
    title        TEXT,
    description  TEXT,
    tags_json    TEXT,
    published_at TEXT,
    duration_s   INTEGER,
    is_short     INTEGER CHECK (is_short IN (0, 1)),
    category_id  TEXT
);
CREATE INDEX idx_videos_channel_published ON videos(channel_id, published_at);

CREATE TABLE video_snapshots (
    id          INTEGER PRIMARY KEY,
    video_id    TEXT NOT NULL REFERENCES videos(id),
    captured_at TEXT NOT NULL,
    views       INTEGER,
    likes       INTEGER,
    comments    INTEGER
);
CREATE INDEX idx_video_snapshots_video_captured ON video_snapshots(video_id, captured_at);

CREATE TABLE transcripts (
    video_id   TEXT NOT NULL,
    language   TEXT NOT NULL,
    text       TEXT,
    source     TEXT,
    status     TEXT,
    fetched_at TEXT,
    PRIMARY KEY (video_id, language)
);

-- A window instead of a day: 011 pulls per-video totals over a date range.
CREATE TABLE own_analytics (
    video_id            TEXT NOT NULL,
    window_start        TEXT NOT NULL,
    window_end          TEXT NOT NULL,
    views               INTEGER,
    est_revenue_usd     REAL,
    rpm_usd             REAL,
    monetized_playbacks INTEGER,
    avg_view_duration_s REAL,
    avg_view_pct        REAL,
    impressions         INTEGER,
    ctr                 REAL,
    sub_delta           INTEGER,
    traffic_json        TEXT,
    PRIMARY KEY (video_id, window_start, window_end)
);

CREATE TABLE niches (
    id                  INTEGER PRIMARY KEY,
    format              TEXT NOT NULL,
    topic               TEXT NOT NULL,
    topic_category      TEXT,
    label               TEXT,
    status              TEXT,
    source              TEXT CHECK (source IN ('llm', 'snowball', 'seed')),
    created_at          TEXT NOT NULL,
    queries_json        TEXT,
    required_steps_json TEXT,
    UNIQUE (format, topic)
);

CREATE TABLE niche_channels (
    niche_id   INTEGER NOT NULL REFERENCES niches(id),
    channel_id TEXT NOT NULL REFERENCES channels(id),
    is_small   INTEGER CHECK (is_small IN (0, 1)),
    added_at   TEXT NOT NULL,
    PRIMARY KEY (niche_id, channel_id)
);

CREATE TABLE niche_scores (
    id                         INTEGER PRIMARY KEY,
    niche_id                   INTEGER NOT NULL REFERENCES niches(id),
    scored_at                  TEXT NOT NULL,
    opportunity                REAL,
    small_outlier_rate         REAL,
    newcomer_view_share        REAL,
    concentration              REAL,
    newcomer_monthly_views_p25 REAL,
    newcomer_monthly_views_p50 REAL,
    newcomer_monthly_views_p75 REAL,
    rpm_gbp                    REAL,
    est_monthly_gbp            REAL,
    manual_hours_per_month     REAL,
    score                      REAL,
    confidence_flags_json      TEXT
);
CREATE INDEX idx_niche_scores_niche_scored ON niche_scores(niche_id, scored_at);

CREATE TABLE competitor_analyses (
    id             INTEGER PRIMARY KEY,
    run_at         TEXT NOT NULL,
    prompt_hash    TEXT NOT NULL,
    schema_version TEXT,
    packet_path    TEXT,
    result_json    TEXT,
    status         TEXT
);

CREATE TABLE video_summaries (
    video_id     TEXT NOT NULL,
    prompt_hash  TEXT NOT NULL,
    summary_json TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (video_id, prompt_hash)
);

CREATE TABLE quota_ledger (
    day_pacific TEXT PRIMARY KEY,
    units_used  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    kind        TEXT NOT NULL,
    status      TEXT,
    log_path    TEXT
);

-- 002 additions -----------------------------------------------------------------------

-- Per channel x window x format metrics (010); appended, the dashboard reads the latest.
CREATE TABLE channel_metrics (
    id           INTEGER PRIMARY KEY,
    channel_id   TEXT NOT NULL REFERENCES channels(id),
    computed_at  TEXT NOT NULL,
    window       TEXT NOT NULL,
    format       TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);
CREATE INDEX idx_channel_metrics_channel_computed ON channel_metrics(channel_id, computed_at);

-- ETag cache for Data API responses (003).
CREATE TABLE api_cache (
    key        TEXT PRIMARY KEY,
    etag       TEXT,
    body_json  TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Resume checkpoints for collectors split across quota days (032).
CREATE TABLE collector_state (
    kind    TEXT NOT NULL,
    key     TEXT NOT NULL,
    done_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

-- Approve/reject/watch clicks from `ytscout serve` (008).
CREATE TABLE decisions (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    decision   TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

-- Append-only guard: 12-month history depends on snapshots never changing.
CREATE TRIGGER channel_snapshots_no_update BEFORE UPDATE ON channel_snapshots
BEGIN SELECT RAISE(ABORT, 'channel_snapshots is append-only'); END;
CREATE TRIGGER channel_snapshots_no_delete BEFORE DELETE ON channel_snapshots
BEGIN SELECT RAISE(ABORT, 'channel_snapshots is append-only'); END;
CREATE TRIGGER video_snapshots_no_update BEFORE UPDATE ON video_snapshots
BEGIN SELECT RAISE(ABORT, 'video_snapshots is append-only'); END;
CREATE TRIGGER video_snapshots_no_delete BEFORE DELETE ON video_snapshots
BEGIN SELECT RAISE(ABORT, 'video_snapshots is append-only'); END;
