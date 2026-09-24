"""``analyse --summaries``: one ``claude -p`` call per video, results into ``video_summaries``.

Candidates are videos of the own and ``approved``/``watch`` channels that have had a
transcript attempt and no summary under the current prompt hash (``repo.summary_candidates``
has the exact rule). Each video: build the packet, write it under ``data/packets/``, run
Claude, store the validated output with both hashes and commit. A call that fails stops
the run and is reported; what was stored before it stays.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ytscout import claude_runner, packets
from ytscout.store import repo

PROMPT_RELPATH = Path("prompts") / "video_summary.md"
SCHEMA_RELPATH = Path("schemas") / "video_summary.json"
PACKET_KIND = "video_summary"
DEFAULT_LIMIT = 40


@dataclass
class SummaryCounts:
    done: int = 0
    candidates: int = 0
    duration_s: float = 0.0
    total_cost_usd: float = 0.0
    # Set when a call failed; the run stops there.
    failure: str | None = None
    failed_video_id: str | None = None
    done_ids: list[str] = field(default_factory=list)


def summary_paths(repo_root: Path) -> tuple[Path, Path]:
    return repo_root / PROMPT_RELPATH, repo_root / SCHEMA_RELPATH


def summarise_videos(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    packets_dir: Path,
    limit: int = DEFAULT_LIMIT,
    model: str | None = None,
    prompt_path: Path | None = None,
    schema_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> SummaryCounts:
    """Summarise up to ``limit`` candidate videos. Caller has checked ``claude`` is usable."""
    default_prompt, default_schema = summary_paths(repo_root)
    prompt_path = Path(prompt_path or default_prompt)
    schema_path = Path(schema_path or default_schema)
    prompt_hash = claude_runner.file_hash(prompt_path)
    counts = SummaryCounts()
    ids = repo.summary_candidates(conn, prompt_hash, limit)
    counts.candidates = len(ids)
    for video_id in ids:
        packet = packets.video_packet(conn, video_id)
        packet_path = packets.write_packet(PACKET_KIND, packet, packets_dir)
        try:
            result = claude_runner.run(
                prompt_path, packet_path, schema_path, model=model, cwd=repo_root
            )
        except claude_runner.ClaudeUnavailable as exc:
            counts.failure = str(exc)
            counts.failed_video_id = video_id
            break
        with conn:
            repo.put_video_summary(
                conn,
                video_id,
                prompt_hash=result.prompt_hash,
                schema_hash=result.schema_hash,
                transcript_status=packet["transcript_status"],
                summary=result.structured_output,
            )
        counts.done += 1
        counts.done_ids.append(video_id)
        counts.duration_s += result.duration_s
        counts.total_cost_usd += result.total_cost_usd or 0.0
        if progress:
            progress(
                f"  {video_id}: {result.structured_output.get('hook_type', '?')} "
                f"({result.duration_s:.0f}s, transcript {packet['transcript_status']})"
            )
    return counts
