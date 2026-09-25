"""The plain inputs to the niche scorers (DESIGN.md §6): a sample of a niche.

No DB, no network. Issue 024 builds these from ``channels`` / ``videos`` rows; the tests
build them by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

Format = str  # "shorts" | "longform"

FORMATS: tuple[str, ...] = ("shorts", "longform")


@dataclass(frozen=True)
class VideoSample:
    """One video: its latest view count, publish date and duration. ``video_id`` only lets
    the dashboard (025) link an outlier; the scorers never read it."""

    views: int
    published_at: datetime
    duration_s: int | None = None
    video_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ChannelSample:
    """One channel in a niche sample.

    ``subs`` is ``None`` when the channel hides its subscriber count and ``created_at`` is
    ``None`` when the API gave no creation date: either makes "small" unknown.
    """

    channel_id: str
    subs: int | None
    created_at: datetime | None
    videos: list[VideoSample] = field(default_factory=list)


@dataclass(frozen=True)
class NicheSample:
    """Every channel sampled for one niche, the format being scored and the scoring date."""

    channels: list[ChannelSample]
    fmt: Format
    now: datetime
