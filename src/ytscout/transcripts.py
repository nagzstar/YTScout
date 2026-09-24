"""Transcripts via ``youtube-transcript-api`` (1.x): the one grey-area dependency.

DESIGN.md decision 13 and §8.3. Everything that touches the library lives in this module so
it can be swapped or removed without touching anything else. Tests replace ``_fetcher``
(the library boundary) and ``_sleep``.

``fetch`` never raises for a transcript problem; it returns a ``Transcript`` whose
``status`` says what happened:

* ``ok`` — a track was found and fetched;
* ``unavailable`` — captions are disabled, there is no track, or the video is gone. Final:
  the collector never asks again;
* ``error`` — network, rate limit or anything unexpected, after 3 retries with backoff
  (1 s, 4 s, 16 s). The next weekly run tries again.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

STATUSES = ("ok", "unavailable", "error")
BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0)
DEFAULT_PAUSE_SECONDS = 1.5
# Stored in the ``language`` key column when no track was fetched (it is NOT NULL).
NO_LANGUAGE = ""

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Transcript:
    video_id: str
    language: str
    text: str | None
    source: str | None  # 'manual' or 'auto'; None when nothing was fetched
    status: str
    detail: str | None = None  # the failure's exception class, for the log; never stored


class Snippet(Protocol):
    text: str


class Track(Protocol):
    """What the library's ``Transcript`` object gives us: enough to choose and fetch."""

    language_code: str
    is_generated: bool

    def fetch(self) -> Iterable[Snippet]: ...


class Unavailable(Exception):
    """Final: this video has no transcript we can use. Never retried."""


def _library_fetcher(video_id: str) -> Iterable[Track]:
    """The real boundary: list a video's caption tracks, mapping final errors to ``Unavailable``."""
    from youtube_transcript_api import (
        InvalidVideoId,
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        YouTubeTranscriptApi,
    )

    try:
        return list(YouTubeTranscriptApi().list(video_id))
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, InvalidVideoId) as exc:
        raise Unavailable(type(exc).__name__) from exc


def _is_final(exc: BaseException) -> bool:
    """A library exception that means "no transcript", raised from ``Track.fetch`` too."""
    if isinstance(exc, Unavailable):
        return True
    try:
        from youtube_transcript_api import (
            InvalidVideoId,
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )
    except ImportError:  # pragma: no cover - the library is a hard dependency
        return False
    return isinstance(
        exc, TranscriptsDisabled | NoTranscriptFound | VideoUnavailable | InvalidVideoId
    )


# Seams: tests replace these. ``_fetcher(video_id)`` returns the video's tracks.
_fetcher: Callable[[str], Iterable[Track]] = _library_fetcher
_sleep: Callable[[float], None] = time.sleep


def choose(tracks: Sequence[Track]) -> Track | None:
    """Preference: manual en, manual en-GB, generated en, any manual, any generated."""
    manual = [t for t in tracks if not t.is_generated]
    generated = [t for t in tracks if t.is_generated]
    for pool, code in ((manual, "en"), (manual, "en-GB"), (generated, "en")):
        for track in pool:
            if track.language_code == code:
                return track
    if manual:
        return manual[0]
    if generated:
        return generated[0]
    return None


def join_snippets(snippets: Iterable[Snippet]) -> str:
    """Segment texts joined with spaces, timestamps dropped, whitespace collapsed."""
    return _WHITESPACE.sub(" ", " ".join(s.text for s in snippets)).strip()


def _attempt(video_id: str) -> Transcript:
    track = choose(list(_fetcher(video_id)))
    if track is None:
        raise Unavailable("no caption tracks")
    text = join_snippets(track.fetch())
    return Transcript(
        video_id=video_id,
        language=track.language_code,
        text=text,
        source="auto" if track.is_generated else "manual",
        status="ok",
    )


def fetch(video_id: str, backoff: Sequence[float] = BACKOFF_SECONDS) -> Transcript:
    """One video's transcript. Unavailable is final at once; anything else is retried
    ``len(backoff)`` times, sleeping ``backoff[i]`` before retry ``i``, then ``error``."""
    attempts = len(backoff) + 1
    for attempt in range(attempts):
        try:
            return _attempt(video_id)
        except Exception as exc:  # noqa: BLE001 - the library's failures are not all typed
            if _is_final(exc):
                detail = str(exc) if isinstance(exc, Unavailable) else type(exc).__name__
                return Transcript(video_id, NO_LANGUAGE, None, None, "unavailable", detail)
            if attempt == attempts - 1:
                return Transcript(video_id, NO_LANGUAGE, None, None, "error", type(exc).__name__)
            _sleep(backoff[attempt])
    raise AssertionError("unreachable")  # pragma: no cover
