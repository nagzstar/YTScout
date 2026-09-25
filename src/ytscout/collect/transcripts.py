"""``collect --transcripts``: fetch missing transcripts once per video; the DB is the cache.

Candidates are videos of the own and ``approved``/``watch`` channels with no ``ok`` or
``unavailable`` row and no ``blocked`` row younger than a week, newest first. Each attempt
writes a row and commits, so an interrupted run keeps what it fetched. No Data API units:
the library does not use the quota.

Circuit breaker (039): after ``BLOCK_STREAK_LIMIT`` consecutive ``blocked`` results the run
stops; the untouched candidates keep whatever rows they had. No pause follows a blocked
result: a refused request is not load worth spacing out.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ytscout import transcripts
from ytscout.store import repo

DEFAULT_LIMIT = 100
BLOCK_STREAK_LIMIT = 5


@dataclass
class TranscriptCounts:
    ok: int = 0
    unavailable: int = 0
    blocked: int = 0
    error: int = 0
    # Set when the circuit breaker tripped: the streak length and the candidates not tried.
    stopped_after: int | None = None
    untouched: int = 0
    # (video_id, status, detail) for every non-ok result: the library's exception class.
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, status: str) -> None:
        setattr(self, status, getattr(self, status) + 1)


def collect_transcripts(
    conn: sqlite3.Connection,
    *,
    limit: int = DEFAULT_LIMIT,
    pause_seconds: float = transcripts.DEFAULT_PAUSE_SECONDS,
) -> TranscriptCounts:
    """Fetch up to ``limit`` candidates, pausing ``pause_seconds`` between videos, until
    ``BLOCK_STREAK_LIMIT`` blocks in a row."""
    counts = TranscriptCounts()
    candidates = repo.transcript_candidates(conn, limit)
    streak = 0
    for i, video_id in enumerate(candidates):
        if streak >= BLOCK_STREAK_LIMIT:
            counts.stopped_after = streak
            counts.untouched = len(candidates) - i
            break
        if i and streak == 0:
            transcripts._sleep(pause_seconds)
        result = transcripts.fetch(video_id)
        with conn:
            repo.put_transcript(
                conn,
                video_id,
                language=result.language,
                text=result.text,
                source=result.source,
                status=result.status,
            )
        counts.add(result.status)
        streak = streak + 1 if result.status == "blocked" else 0
        if result.status != "ok":
            counts.failures.append((video_id, result.status, result.detail or "?"))
    return counts
