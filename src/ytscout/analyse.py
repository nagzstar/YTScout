"""The two ``analyse`` stages: per-video summaries, then the competitor comparison.

``analyse --summaries``: one ``claude -p`` call per video, results into ``video_summaries``.
Candidates are videos of the own and ``approved``/``watch`` channels that have had a
transcript attempt and no summary under the current prompt hash (``repo.summary_candidates``
has the exact rule). Each video: build the packet, write it under ``data/packets/``, run
Claude, store the validated output with both hashes and commit. A call that fails stops
the run and is reported; what was stored before it stays.

``analyse --competitors``: one call over the summaries of the own and approved channels,
grounded (every cited id must be in the packet) and stored in ``competitor_analyses``.
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


# --- analyse --competitors (017) -------------------------------------------------------------

COMPETITOR_PROMPT_RELPATH = Path("prompts") / "competitor_analysis.md"
COMPETITOR_SCHEMA_RELPATH = Path("schemas") / "competitor_analysis.json"
COMPETITOR_PACKET_KIND = "competitors"
STATUS_OK = "ok"
STATUS_PENDING = "pending"
_VIDEO_ID_LIST_SUFFIX = "_video_ids"
_CHANNEL_ID_LIST_SUFFIX = "_channel_ids"


@dataclass
class CompetitorOutcome:
    row_id: int
    status: str
    packet_path: Path | None = None
    channels: int = 0
    videos: int = 0
    dropped_video_ids: list[str] = field(default_factory=list)
    dropped_channel_ids: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    total_cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    failure: str | None = None


def competitor_paths(repo_root: Path) -> tuple[Path, Path]:
    return repo_root / COMPETITOR_PROMPT_RELPATH, repo_root / COMPETITOR_SCHEMA_RELPATH


def ground_analysis(
    analysis: dict, video_ids: set[str], channel_ids: set[str]
) -> tuple[dict, list[str], list[str]]:
    """A copy of ``analysis`` citing only packet ids, plus what was dropped.

    Every list under a key ending in ``_video_ids`` keeps only ids in ``video_ids``; every
    list under a key ending in ``_channel_ids`` keeps only ids in ``channel_ids``; a
    ``per_competitor`` entry whose ``channel_id`` is unknown goes. Dropped ids are counted
    (not named) in ``meta.caveats`` so the dashboard says something was cut.
    """
    dropped_videos: list[str] = []
    dropped_channels: list[str] = []

    def walk(value: object) -> object:
        if isinstance(value, dict):
            out: dict = {}
            for key, item in value.items():
                if key.endswith(_VIDEO_ID_LIST_SUFFIX) and isinstance(item, list):
                    out[key] = _keep(item, video_ids, dropped_videos)
                elif key.endswith(_CHANNEL_ID_LIST_SUFFIX) and isinstance(item, list):
                    out[key] = _keep(item, channel_ids, dropped_channels)
                else:
                    out[key] = walk(item)
            return out
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    grounded = walk(analysis)
    assert isinstance(grounded, dict)
    kept = []
    for entry in grounded.get("per_competitor", []):
        if isinstance(entry, dict) and entry.get("channel_id") not in channel_ids:
            dropped_channels.append(str(entry.get("channel_id")))
        else:
            kept.append(entry)
    grounded["per_competitor"] = kept

    meta = grounded.setdefault("meta", {})
    caveats = list(meta.get("caveats") or [])
    unique_videos = sorted(set(dropped_videos))
    unique_channels = sorted(set(dropped_channels))
    # Counts only: the row must never carry an id the packet did not (the CLI logs them).
    if unique_videos:
        caveats.append(
            f"{len(unique_videos)} cited video id(s) were not in the packet and were dropped."
        )
    if unique_channels:
        caveats.append(
            f"{len(unique_channels)} cited channel id(s) were not in the packet and were dropped."
        )
    meta["caveats"] = [truncate_caveat(c) for c in caveats]
    return grounded, unique_videos, unique_channels


def truncate_caveat(text: str) -> str:
    return packets.truncate(text, 200)


def _keep(items: list, known: set[str], dropped: list[str]) -> list:
    kept = []
    for item in items:
        if isinstance(item, str) and item in known:
            kept.append(item)
        else:
            dropped.append(str(item))
    return kept


def record_pending(
    conn: sqlite3.Connection,
    *,
    prompt_hash: str,
    schema_hash: str,
    packet_path: Path | None,
    reason: str,
) -> int:
    """Write the ``pending`` row the dashboard shows when ``claude`` could not run."""
    with conn:
        return repo.put_competitor_analysis(
            conn,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            packet_path=None if packet_path is None else str(packet_path),
            result={"error": reason},
            status=STATUS_PENDING,
        )


def analyse_competitors(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    packets_dir: Path,
    model: str | None = None,
    prompt_path: Path | None = None,
    schema_path: Path | None = None,
) -> CompetitorOutcome:
    """One comparison call: packet → ``claude -p`` → grounded row in ``competitor_analyses``.

    Caller has checked ``claude`` is usable. A ``ClaudeUnavailable`` from the call is
    recorded as a ``pending`` row and reported in ``failure``; nothing is raised.
    """
    default_prompt, default_schema = competitor_paths(repo_root)
    prompt_path = Path(prompt_path or default_prompt)
    schema_path = Path(schema_path or default_schema)
    prompt_hash = claude_runner.file_hash(prompt_path)
    schema_hash = claude_runner.file_hash(schema_path)
    packet = packets.competitor_packet(conn)
    packet_path = packets.write_packet(COMPETITOR_PACKET_KIND, packet, packets_dir)
    video_ids = packets.packet_video_ids(packet)
    channel_ids = packets.packet_channel_ids(packet)
    try:
        result = claude_runner.run(
            prompt_path, packet_path, schema_path, model=model, cwd=repo_root
        )
    except claude_runner.ClaudeUnavailable as exc:
        row_id = record_pending(
            conn,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            packet_path=packet_path,
            reason=str(exc),
        )
        return CompetitorOutcome(
            row_id,
            STATUS_PENDING,
            packet_path=packet_path,
            channels=len(channel_ids),
            videos=len(video_ids),
            failure=str(exc),
        )
    grounded, dropped_videos, dropped_channels = ground_analysis(
        result.structured_output, video_ids, channel_ids
    )
    with conn:
        row_id = repo.put_competitor_analysis(
            conn,
            prompt_hash=result.prompt_hash,
            schema_hash=result.schema_hash,
            packet_path=str(packet_path),
            result=grounded,
            status=STATUS_OK,
        )
    return CompetitorOutcome(
        row_id,
        STATUS_OK,
        packet_path=packet_path,
        channels=len(channel_ids),
        videos=len(video_ids),
        dropped_video_ids=dropped_videos,
        dropped_channel_ids=dropped_channels,
        duration_s=result.duration_s,
        total_cost_usd=result.total_cost_usd or 0.0,
        usage=result.usage,
    )
