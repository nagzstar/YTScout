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


# --- competitor packet (017) -----------------------------------------------------------------

COMPETITOR_VIDEOS = 15
COMPETITOR_VIDEOS_REDUCED = 10
COMPETITOR_PACKET_MAX_BYTES = 150_000
COMPETITOR_WINDOW = "90d"
COMPETITOR_FORMAT = "shorts"
# Video ids the metrics carry need not be summarised videos; the prompt says every cited
# id must be a packet video, so the list stays out.
_METRIC_KEYS_DROPPED = ("outlier_ids",)


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _competitor_channel(
    conn: sqlite3.Connection,
    channel: sqlite3.Row,
    metrics: dict | None,
    videos_per_channel: int,
) -> dict:
    snap = repo.latest_channel_snapshot(conn, channel["id"])
    videos = []
    for row in repo.summarised_videos_for_channel(conn, channel["id"], videos_per_channel):
        videos.append(
            {
                "video_id": row["id"],
                "title": row["title"],
                "views": row["views"],
                "published_at": row["published_at"],
                "duration_s": row["duration_s"],
                "transcript_status": row["transcript_status"],
                "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
            }
        )
    views = [v["views"] for v in videos if v["views"] is not None]
    return {
        "id": channel["id"],
        "title": channel["title"],
        "role": "own" if channel["role"] == "own" else "competitor",
        "subs": snap["subs"] if snap else None,
        "metrics": metrics,
        "views_median": _median(views),
        "videos": videos,
    }


def competitor_packet(conn: sqlite3.Connection) -> dict:
    """The own channel and every ``approved`` channel with their summarised videos.

    Each channel carries its latest 90-day Shorts ``channel_metrics`` (``None`` when not
    scored) and its last ``COMPETITOR_VIDEOS`` summarised videos. When the JSON would be
    over ``COMPETITOR_PACKET_MAX_BYTES`` the packet is rebuilt with
    ``COMPETITOR_VIDEOS_REDUCED`` per channel and ``meta.reduced`` says so.
    """
    channels = [
        c for c in repo.tracked_channels(conn) if c["role"] == "own" or c["status"] == "approved"
    ]
    metrics: dict[str, dict] = {}
    for row in repo.latest_channel_metrics(conn):
        if row["window"] == COMPETITOR_WINDOW and row["format"] == COMPETITOR_FORMAT:
            m = json.loads(row["metrics_json"])
            for key in _METRIC_KEYS_DROPPED:
                m.pop(key, None)
            metrics[row["channel_id"]] = m
    own_id = next((c["id"] for c in channels if c["role"] == "own"), None)

    def build(per_channel: int, reduced: bool) -> dict:
        note = None
        if reduced:
            note = (
                f"videos per channel cut from {COMPETITOR_VIDEOS} to {per_channel} to keep "
                f"the packet under {COMPETITOR_PACKET_MAX_BYTES:,} bytes"
            )
        return {
            "own_channel_id": own_id,
            "meta": {
                "built_at": utc_now().isoformat(timespec="seconds").replace("+00:00", "Z"),
                "metrics_window": COMPETITOR_WINDOW,
                "metrics_format": COMPETITOR_FORMAT,
                "videos_per_channel": per_channel,
                "reduced": reduced,
                "note": note,
            },
            "channels": [
                _competitor_channel(conn, c, metrics.get(c["id"]), per_channel) for c in channels
            ],
        }

    packet = build(COMPETITOR_VIDEOS, reduced=False)
    if len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) > COMPETITOR_PACKET_MAX_BYTES:
        packet = build(COMPETITOR_VIDEOS_REDUCED, reduced=True)
    return packet


def packet_video_ids(packet: dict) -> set[str]:
    """Every ``video_id`` a competitor packet lists."""
    return {v["video_id"] for c in packet.get("channels", []) for v in c.get("videos", [])}


def packet_channel_ids(packet: dict) -> set[str]:
    return {c["id"] for c in packet.get("channels", [])}
