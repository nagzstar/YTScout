"""``DataApi``: the YouTube Data API v3 calls the project makes, each charged first.

Costs come from ``UNIT_COSTS`` in ``quota.py``; no method writes a number inline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

from ytscout.store import repo
from ytscout.store.db import to_utc_iso
from ytscout.youtube.quota import UNIT_COSTS, Ledger
from ytscout.youtube.transport import NotModified, Transport

MAX_IDS_PER_CALL = 50
PAGE_SIZE = 50
_PARTS = "snippet,statistics,contentDetails"
_DURATION = re.compile(
    r"^P(?:(?P<w>\d+)W)?(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def parse_duration(value: str) -> int:
    """ISO-8601 duration to seconds: ``PT1M3S`` -> 63, ``P0D`` -> 0."""
    match = _DURATION.match(value)
    if not match or value == "P" or value.endswith("T"):
        raise ValueError(f"not an ISO-8601 duration: {value!r}")
    n = {k: int(v or 0) for k, v in match.groupdict().items()}
    return (((n["w"] * 7 + n["d"]) * 24 + n["h"]) * 60 + n["m"]) * 60 + n["s"]


def parse_dt(value: str) -> datetime:
    """An API timestamp (``2026-01-02T03:04:05Z``) as an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {value!r}")
    return parsed.astimezone(UTC)


def cache_key(resource: str, method: str, params: dict[str, Any]) -> str:
    return f"{resource}.{method}?{json.dumps(params, sort_keys=True)}"


def _ids(ids: Sequence[str]) -> str:
    if not ids:
        raise ValueError("no ids given")
    if len(ids) > MAX_IDS_PER_CALL:
        raise ValueError(f"{len(ids)} ids; the API takes at most {MAX_IDS_PER_CALL} per call")
    return ",".join(ids)


class DataApi:
    """Charges the ledger, then calls the transport.

    ETags: every response is cached in ``api_cache`` under resource+method+params, and the
    next identical call sends ``If-None-Match``; on 304 the cached body is returned.
    **The charge still applies on a 304.** YouTube bills the request either way, so do not
    "optimise" the charge away for cache hits: the cache saves bandwidth, not quota.
    Under a dry-run ledger the cache is neither read nor written.
    """

    def __init__(self, ledger: Ledger, transport: Transport) -> None:
        self.ledger = ledger
        self.transport = transport
        self.use_cache = not ledger.dry_run

    def _request(self, resource: str, method: str, **params: Any) -> dict:
        units = UNIT_COSTS[(resource, method)]
        conn = self.ledger.conn
        key = cache_key(resource, method, params)
        cached = repo.get_api_cache(conn, key) if self.use_cache else None
        etag = cached[0] if cached else None
        self.ledger.charge(units)
        try:
            body = self.transport.call(resource, method, etag=etag, **params)
        except NotModified:
            if cached is None:
                raise
            return cached[1]
        if self.use_cache:
            with conn:
                repo.put_api_cache(conn, key, body.get("etag"), body)
        return body

    def search(
        self,
        q: str,
        *,
        order: str,
        published_after: datetime | str,
        video_duration: str | None = None,
        max_results: int = 50,
    ) -> dict:
        """``search.list`` for videos. 100 units: use a channel or playlist call if one will do."""
        if isinstance(published_after, datetime):
            published_after = to_utc_iso(published_after)
        params: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "q": q,
            "order": order,
            "publishedAfter": published_after,
            "maxResults": max_results,
        }
        if video_duration is not None:
            params["videoDuration"] = video_duration
        return self._request("search", "list", **params)

    def channels(self, ids: Sequence[str], *, part: str = _PARTS) -> dict:
        """``channels.list`` for up to 50 ids in one call. Every ``part`` costs the same unit."""
        return self._request("channels", "list", part=part, id=_ids(ids), maxResults=50)

    def playlist_items(self, playlist_id: str, *, page_token: str | None = None) -> dict:
        """One page (up to 50 items) of ``playlistItems.list``."""
        params: dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": PAGE_SIZE,
        }
        if page_token is not None:
            params["pageToken"] = page_token
        return self._request("playlistItems", "list", **params)

    def iter_playlist_items(
        self, playlist_id: str, *, max_pages: int | None = None
    ) -> Iterator[dict]:
        """Every item of a playlist, following ``nextPageToken``. One charge per page."""
        token: str | None = None
        pages = 0
        while max_pages is None or pages < max_pages:
            page = self.playlist_items(playlist_id, page_token=token)
            pages += 1
            yield from page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                return

    def videos(self, ids: Sequence[str]) -> dict:
        """``videos.list`` for up to 50 ids in one call."""
        return self._request("videos", "list", part=_PARTS, id=_ids(ids), maxResults=50)
