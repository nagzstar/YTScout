-- 0011: transcripts gain a `blocked` status (039): YouTube refused the request
-- (IpBlocked/RequestBlocked). A blocked row keeps its video out of the candidates for 7 days
-- after `fetched_at`; `error` rows stay candidates every run. The status column had no
-- constraint, so the table is rebuilt with one that names every status.
CREATE TABLE transcripts_new (
    video_id   TEXT NOT NULL,
    language   TEXT NOT NULL,
    text       TEXT,
    source     TEXT,
    status     TEXT CHECK (status IN ('ok', 'unavailable', 'blocked', 'error')),
    fetched_at TEXT,
    PRIMARY KEY (video_id, language)
);
INSERT INTO transcripts_new (video_id, language, text, source, status, fetched_at)
    SELECT video_id, language, text, source, status, fetched_at FROM transcripts;
DROP TABLE transcripts;
ALTER TABLE transcripts_new RENAME TO transcripts;
