"""Analysis packets: compact JSON files Claude reads by path.

``video_packet`` gathers one video from SQLite into a plain dict small enough to run
forty times a week; ``write_packet`` files it under ``data/packets/`` as
``YYYY-MM-DD-<kind>-<n>.json``. Long fields are cut with a visible marker so the reader
knows text is missing rather than short.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ytscout.store import repo, utc_now

DESCRIPTION_MAX = 1_000
TRANSCRIPT_MAX = 6_000
TRUNCATED = "…[truncated]"
PACKETS_SUBDIR = "packets"
TRANSCRIPT_MISSING = "missing"

_KIND = re.compile(r"^[a-z0-9_]+$")


def truncate(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters, ending in ``TRUNCATED`` if it was cut."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(TRUNCATED))] + TRUNCATED


def transcript_for_packet(conn: sqlite3.Connection, video_id: str) -> tuple[str | None, str]:
    """``(text, status)``: the ``ok`` transcript's text, else ``None`` and why.

    With no ``ok`` row the status is the most final one recorded (``unavailable`` over
    ``error``), or ``missing`` when transcripts were never attempted.
    """
    rows = repo.get_transcripts(conn, video_id)
    for row in rows:
        if row["status"] == "ok" and row["text"]:
            return row["text"], "ok"
    statuses = {row["status"] for row in rows}
    for status in ("unavailable", "error"):
        if status in statuses:
            return None, status
    return None, TRANSCRIPT_MISSING


def video_packet(conn: sqlite3.Connection, video_id: str) -> dict:
    """Everything the per-video summary prompt may use, from SQLite only.

    Raises ``LookupError`` when the video is not in the DB.
    """
    video = repo.get_video(conn, video_id)
    if video is None:
        raise LookupError(f"no video {video_id!r} in the database")
    channel = repo.get_channel(conn, video["channel_id"])
    channel_snap = repo.latest_channel_snapshot(conn, video["channel_id"])
    video_snap = repo.latest_video_snapshot(conn, video_id)
    text, status = transcript_for_packet(conn, video_id)
    tags = json.loads(video["tags_json"]) if video["tags_json"] else []
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "channel": {
            "id": video["channel_id"],
            "title": channel["title"] if channel else None,
            "subs": channel_snap["subs"] if channel_snap else None,
        },
        "title": video["title"],
        "description": truncate(video["description"] or "", DESCRIPTION_MAX),
        "tags": tags,
        "duration_s": video["duration_s"],
        "is_short": None if video["is_short"] is None else bool(video["is_short"]),
        "published_at": video["published_at"],
        "stats": {
            "captured_at": video_snap["captured_at"] if video_snap else None,
            "views": video_snap["views"] if video_snap else None,
            "likes": video_snap["likes"] if video_snap else None,
            "comments": video_snap["comments"] if video_snap else None,
        },
        "transcript_status": status,
        "transcript": None if text is None else truncate(text, TRANSCRIPT_MAX),
    }


def packets_dir(data_dir: Path) -> Path:
    return Path(data_dir) / PACKETS_SUBDIR


def write_packet(kind: str, payload: dict, directory: Path) -> Path:
    """Write ``payload`` to ``<directory>/YYYY-MM-DD-<kind>-<n>.json`` and return the path.

    ``n`` is one more than the highest number already used for that day and kind, so a
    re-run never overwrites the packet a stored analysis was made from.
    """
    if not _KIND.match(kind):
        raise ValueError(f"packet kind must be [a-z0-9_]+, got {kind!r}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = utc_now().strftime("%Y-%m-%d")
    prefix = f"{day}-{kind}-"
    used = []
    for existing in directory.glob(f"{prefix}*.json"):
        suffix = existing.name[len(prefix) : -len(".json")]
        if suffix.isdigit():
            used.append(int(suffix))
    path = directory / f"{prefix}{max(used, default=0) + 1}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
