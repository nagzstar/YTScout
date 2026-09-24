"""Transports: the one seam between ``DataApi`` and the network.

``GoogleTransport`` is the only code that imports ``googleapiclient``. ``FakeTransport``
answers from fixtures for tests; ``DryRunTransport`` answers every call with an empty
response and records what a real run would have asked for.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any, Protocol

from googleapiclient.errors import HttpError

from ytscout.youtube.quota import UNIT_COSTS, QuotaExhausted

# Google error reasons that mean the project's daily quota is spent.
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}

FrozenParams = tuple[tuple[str, Any], ...]


class NotModified(Exception):
    """The server answered 304 to ``If-None-Match``: the cached body is still current."""


class Transport(Protocol):
    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        """Run ``youtube.<resource>().<method>(**params)``.

        ``etag``, when given, is sent as ``If-None-Match``; a 304 raises ``NotModified``.
        """
        ...


def freeze(params: Mapping[str, Any]) -> FrozenParams:
    """A hashable, order-independent form of ``params`` (lists become tuples)."""
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in params.items()))


def _is_quota_exceeded(exc: HttpError) -> bool:
    try:
        body = json.loads(exc.content.decode("utf-8"))
        errors = body["error"].get("errors", [])
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    return any(isinstance(e, dict) and e.get("reason") in _QUOTA_REASONS for e in errors)


class GoogleTransport:
    """The real thing. The discovery client is built on first use, not at construction."""

    def __init__(self, api_key: str | None, *, service: Any = None) -> None:
        if not api_key and service is None:
            raise ValueError("no YouTube API key: set YT_API_KEY in .env (see ytscout doctor)")
        self._api_key = api_key
        self._service = service

    def __repr__(self) -> str:
        return "GoogleTransport(api_key=***)"

    def _youtube(self) -> Any:
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "youtube", "v3", developerKey=self._api_key, cache_discovery=False
            )
        return self._service

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        request = getattr(getattr(self._youtube(), resource)(), method)(**params)
        if etag:
            request.headers["If-None-Match"] = etag
        try:
            return request.execute()
        except HttpError as exc:
            if exc.resp.status == 304:
                raise NotModified() from exc
            if _is_quota_exceeded(exc):
                # The Cloud project may be shared with the production pipeline, whose
                # uploads the ledger cannot see.
                raise QuotaExhausted("google") from exc
            raise


class FakeTransport:
    """Answers from ``fixtures[(resource, method, freeze(params))]``.

    A fixture is a response dict, or a list of pages. For a list, the key omits
    ``pageToken``; page *i* is returned with ``nextPageToken`` pointing at page *i+1*
    (the page's own ``nextPageToken`` if it has one, else ``"page<i+1>"``) and the last
    page has none. A response carrying ``etag`` raises ``NotModified`` when called with
    that etag. A missing fixture raises ``KeyError`` naming the call.
    """

    def __init__(self, fixtures: Mapping[tuple[str, str, FrozenParams], Any]) -> None:
        self.fixtures = dict(fixtures)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    @staticmethod
    def key(resource: str, method: str, **params: Any) -> tuple[str, str, FrozenParams]:
        return (resource, method, freeze(params))

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        self.calls.append((resource, method, dict(params)))
        response = self._lookup(resource, method, params)
        if etag is not None and response.get("etag") == etag:
            raise NotModified()
        return response

    def _lookup(self, resource: str, method: str, params: dict[str, Any]) -> dict:
        exact = self.key(resource, method, **params)
        found = self.fixtures.get(exact)
        if isinstance(found, dict):
            return copy.deepcopy(found)
        rest = {k: v for k, v in params.items() if k != "pageToken"}
        pages = self.fixtures.get(self.key(resource, method, **rest))
        if isinstance(pages, list):
            tokens = [p.get("nextPageToken") or f"page{i + 1}" for i, p in enumerate(pages)]
            token = params.get("pageToken")
            if token is None:
                index = 0
            elif token in tokens[:-1]:
                index = tokens.index(token) + 1
            else:
                index = -1
            if index >= 0:
                page = copy.deepcopy(pages[index])
                page.pop("nextPageToken", None)
                if index < len(pages) - 1:
                    page["nextPageToken"] = tokens[index]
                return page
        raise KeyError(f"no fixture for {resource}.{method}({params!r}); add one keyed {exact!r}")


class DryRunTransport:
    """Never touches the network. Records ``(resource, method, params, units)`` per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], int]] = []

    @property
    def units(self) -> int:
        return sum(call[3] for call in self.calls)

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        self.calls.append((resource, method, dict(params), UNIT_COSTS[(resource, method)]))
        return {"items": []}
