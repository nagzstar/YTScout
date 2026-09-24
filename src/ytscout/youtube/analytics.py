"""YouTube Analytics API v2 access for the own channel.

``AnalyticsApi.query(**params)`` → ``AnalyticsTransport.query`` → rows as dicts keyed by
column name. ``GoogleAnalyticsTransport`` is the only code here that touches the network;
``FakeAnalyticsTransport`` answers tests and ``DryRunAnalyticsTransport`` backs
``--dry-run``. Analytics quota is separate from the Data API and not charged to the ledger.

Revenue metric names (``estimatedRevenue``) appear only here and in ``collect/analytics.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

OWN_CHANNEL = "channel==MINE"

VIDEO_METRICS: tuple[str, ...] = (
    "views",
    "estimatedRevenue",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "subscribersGained",
    "subscribersLost",
)
# Not in the Analytics API's documented metric list (2026-09): thumbnail impressions and CTR
# live in Studio and the bulk Reporting API. Asked for anyway; a rejection drops them.
IMPRESSION_METRICS: tuple[str, ...] = ("impressions", "impressionsClickThroughRate")
DAILY_METRICS: tuple[str, ...] = ("views", "estimatedRevenue", "monetizedPlaybacks")
TRAFFIC_METRICS: tuple[str, ...] = ("views",)


class QueryRejected(Exception):
    """The API refused the query as invalid (HTTP 400), e.g. an unknown metric."""


class AnalyticsTransport(Protocol):
    def query(self, **params: Any) -> dict:
        """Run ``youtubeAnalytics.reports().query(**params)`` and return the raw response."""
        ...


class GoogleAnalyticsTransport:
    """The real thing, authorised by OAuth credentials. The client is built on first use."""

    def __init__(self, credentials: Any, *, service: Any = None) -> None:
        self._credentials = credentials
        self._service = service

    def __repr__(self) -> str:
        return "GoogleAnalyticsTransport(credentials=***)"

    def _analytics(self) -> Any:
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "youtubeAnalytics", "v2", credentials=self._credentials, cache_discovery=False
            )
        return self._service

    def query(self, **params: Any) -> dict:
        from googleapiclient.errors import HttpError

        try:
            return self._analytics().reports().query(**params).execute()
        except HttpError as exc:
            if exc.resp.status == 400:
                raise QueryRejected(_error_message(exc)) from exc
            raise


def _error_message(exc: Any) -> str:
    try:
        return str(json.loads(exc.content.decode("utf-8"))["error"]["message"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return "HTTP 400"


class FakeAnalyticsTransport:
    """Answers with ``handler(params)``; the handler may raise ``QueryRejected``."""

    def __init__(self, handler: Callable[[dict[str, Any]], dict]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def query(self, **params: Any) -> dict:
        self.calls.append(dict(params))
        return self.handler(dict(params))


class DryRunAnalyticsTransport:
    """Never touches the network: records each query and answers with no rows."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query(self, **params: Any) -> dict:
        self.calls.append(dict(params))
        return {"columnHeaders": [], "rows": []}


def rows_as_dicts(response: dict) -> list[dict[str, Any]]:
    """``{"columnHeaders": [{"name": …}], "rows": [[…]]}`` → one dict per row."""
    names = [header["name"] for header in response.get("columnHeaders", [])]
    return [dict(zip(names, row, strict=True)) for row in response.get("rows") or []]


class AnalyticsApi:
    """One method, ``query``: ``ids`` defaults to the authorised channel."""

    def __init__(self, transport: AnalyticsTransport) -> None:
        self.transport = transport

    def query(self, **params: Any) -> list[dict[str, Any]]:
        params.setdefault("ids", OWN_CHANNEL)
        return rows_as_dicts(self.transport.query(**params))
