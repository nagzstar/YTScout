"""DataApi against fake transports: charges, pagination, ETags, dry runs, Google errors."""

from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from ytscout.store import repo
from ytscout.store.db import connect
from ytscout.youtube import (
    UNIT_COSTS,
    DataApi,
    DryRunTransport,
    FakeTransport,
    GoogleTransport,
    Ledger,
    QuotaExhausted,
    parse_dt,
    parse_duration,
)

FIXTURES = Path(__file__).parent / "fixtures" / "data_api"
PARTS = "snippet,statistics,contentDetails"
NOON_UTC = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
DAY = "2026-09-24"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def ledger(conn: sqlite3.Connection, **kwargs: Any) -> Ledger:
    return Ledger(conn, daily_cap=9000, clock=lambda: NOON_UTC, **kwargs)


def vid_ids(n: int) -> list[str]:
    return [f"vid{i:08d}" for i in range(1, n + 1)]


def videos_key(ids: list[str]) -> tuple:
    return FakeTransport.key("videos", "list", part=PARTS, id=",".join(ids), maxResults=50)


def playlist_key(playlist_id: str) -> tuple:
    return FakeTransport.key(
        "playlistItems",
        "list",
        part="snippet,contentDetails",
        playlistId=playlist_id,
        maxResults=50,
    )


# --- costs ---------------------------------------------------------------------------------


def test_cost_table() -> None:
    assert UNIT_COSTS == {
        ("search", "list"): 100,
        ("channels", "list"): 1,
        ("playlistItems", "list"): 1,
        ("videos", "list"): 1,
    }


def test_search_charges_100(conn: sqlite3.Connection) -> None:
    key = FakeTransport.key(
        "search",
        "list",
        part="snippet",
        type="video",
        q="animal countdown",
        order="viewCount",
        publishedAfter="2026-08-01T00:00:00Z",
        maxResults=50,
        videoDuration="short",
    )
    api = DataApi(ledger(conn), FakeTransport({key: {"items": []}}))
    api.search(
        "animal countdown",
        order="viewCount",
        published_after=datetime(2026, 8, 1, tzinfo=UTC),
        video_duration="short",
    )
    assert repo.quota_used(conn, DAY) == 100


def test_videos_with_50_ids_charges_1(conn: sqlite3.Connection) -> None:
    ids = vid_ids(50)
    api = DataApi(ledger(conn), FakeTransport({videos_key(ids): {"items": []}}))
    api.videos(ids)
    assert repo.quota_used(conn, DAY) == 1


def test_videos_and_channels_refuse_more_than_50_ids(conn: sqlite3.Connection) -> None:
    api = DataApi(ledger(conn), FakeTransport({}))
    with pytest.raises(ValueError):
        api.videos(vid_ids(51))
    with pytest.raises(ValueError):
        api.channels([])
    assert repo.quota_used(conn, DAY) == 0


def test_channel_and_videos_fixtures_parse(conn: sqlite3.Connection) -> None:
    channel_key = FakeTransport.key(
        "channels", "list", part=PARTS, id="UCtestchannel0000000001", maxResults=50
    )
    transport = FakeTransport(
        {channel_key: load("channel"), videos_key(vid_ids(5)): load("videos_batch")}
    )
    api = DataApi(ledger(conn), transport)
    channel = api.channels(["UCtestchannel0000000001"])["items"][0]
    assert channel["contentDetails"]["relatedPlaylists"]["uploads"] == "UUtestchannel0000000001"
    videos = api.videos(vid_ids(5))["items"]
    assert [parse_duration(v["contentDetails"]["duration"]) for v in videos] == [
        45,
        59,
        120,
        3723,
        0,
    ]
    assert repo.quota_used(conn, DAY) == 2


def test_missing_fixture_names_the_call(conn: sqlite3.Connection) -> None:
    api = DataApi(ledger(conn), FakeTransport({}))
    with pytest.raises(KeyError, match="videos.list"):
        api.videos(["abc"])


# --- pagination ----------------------------------------------------------------------------


def test_pagination_through_two_fixture_pages(conn: sqlite3.Connection) -> None:
    pages = [load("playlist_items_page1"), load("playlist_items_page2")]
    transport = FakeTransport({playlist_key("UUtestchannel0000000001"): pages})
    api = DataApi(ledger(conn), transport)

    first = api.playlist_items("UUtestchannel0000000001")
    assert len(first["items"]) == 5
    assert first["nextPageToken"] == "CAUQAA"
    second = api.playlist_items("UUtestchannel0000000001", page_token="CAUQAA")
    assert len(second["items"]) == 2
    assert "nextPageToken" not in second

    items = list(api.iter_playlist_items("UUtestchannel0000000001"))
    assert [i["contentDetails"]["videoId"] for i in items] == vid_ids(7)
    assert [c[2].get("pageToken") for c in transport.calls[-2:]] == [None, "CAUQAA"]
    assert repo.quota_used(conn, DAY) == 4  # one unit per page, four pages fetched


def test_fake_transport_invents_tokens_when_pages_have_none(conn: sqlite3.Connection) -> None:
    pages = [{"items": [{"id": "a"}]}, {"items": [{"id": "b"}]}, {"items": [{"id": "c"}]}]
    api = DataApi(ledger(conn), FakeTransport({playlist_key("PL1"): pages}))
    assert [i["id"] for i in api.iter_playlist_items("PL1")] == ["a", "b", "c"]
    assert [i["id"] for i in api.iter_playlist_items("PL1", max_pages=2)] == ["a", "b"]


# --- etags ---------------------------------------------------------------------------------


def test_etag_304_returns_cached_body_and_still_charges(conn: sqlite3.Connection) -> None:
    ids = vid_ids(5)
    transport = FakeTransport({videos_key(ids): load("videos_batch")})
    api = DataApi(ledger(conn), transport)
    first = api.videos(ids)
    transport.fixtures[videos_key(ids)] = {"etag": "etag-videos-1", "items": ["not me"]}
    second = api.videos(ids)
    assert second == first
    assert repo.quota_used(conn, DAY) == 2


def test_changed_etag_replaces_the_cache(conn: sqlite3.Connection) -> None:
    ids = vid_ids(5)
    transport = FakeTransport({videos_key(ids): load("videos_batch")})
    api = DataApi(ledger(conn), transport)
    api.videos(ids)
    transport.fixtures[videos_key(ids)] = {"etag": "etag-videos-2", "items": []}
    assert api.videos(ids) == {"etag": "etag-videos-2", "items": []}
    assert api.videos(ids) == {"etag": "etag-videos-2", "items": []}


# --- quota ---------------------------------------------------------------------------------


def test_exhausted_ledger_stops_before_the_transport(conn: sqlite3.Connection) -> None:
    transport = FakeTransport({videos_key(["a"]): {"items": []}})
    api = DataApi(ledger(conn, run_cap=0), transport)
    with pytest.raises(QuotaExhausted) as info:
        api.videos(["a"])
    assert info.value.kind == "run"
    assert transport.calls == []


class _Request:
    def __init__(self, exc: Exception | None, body: dict | None = None) -> None:
        self.headers: dict[str, str] = {}
        self._exc = exc
        self._body = body

    def execute(self) -> dict:
        if self._exc:
            raise self._exc
        assert self._body is not None
        return self._body


class _Service:
    """Stands in for googleapiclient's discovery object: service.videos().list(**p)."""

    def __init__(self, exc: Exception | None = None, body: dict | None = None) -> None:
        self.exc = exc
        self.body = body
        self.requests: list[_Request] = []

    def videos(self) -> _Service:
        return self

    def list(self, **params: Any) -> _Request:
        request = _Request(self.exc, self.body)
        self.requests.append(request)
        return request


def _http_error(status: int, reason: str | None) -> HttpError:
    resp = httplib2.Response({"status": status})
    resp.reason = "Forbidden"
    errors = [{"reason": reason, "domain": "youtube.quota"}] if reason else []
    content = json.dumps({"error": {"code": status, "message": "x", "errors": errors}})
    return HttpError(resp, content.encode("utf-8"))


def test_google_quota_exceeded_surfaces_as_quota_exhausted_google(
    conn: sqlite3.Connection,
) -> None:
    transport = GoogleTransport(None, service=_Service(_http_error(403, "quotaExceeded")))
    api = DataApi(ledger(conn), transport)
    with pytest.raises(QuotaExhausted) as info:
        api.videos(["a"])
    assert info.value.kind == "google"
    assert repo.quota_used(conn, DAY) == 1  # the refused request was still charged


def test_other_google_errors_pass_through(conn: sqlite3.Connection) -> None:
    transport = GoogleTransport(None, service=_Service(_http_error(403, "forbidden")))
    with pytest.raises(HttpError):
        DataApi(ledger(conn), transport).videos(["a"])


def test_google_transport_sends_if_none_match_and_maps_304(conn: sqlite3.Connection) -> None:
    service = _Service(body={"etag": "e1", "items": [1]})
    api = DataApi(ledger(conn), GoogleTransport(None, service=service))
    assert api.videos(["a"]) == {"etag": "e1", "items": [1]}
    assert service.requests[0].headers == {}
    service.exc = _http_error(304, None)
    assert api.videos(["a"]) == {"etag": "e1", "items": [1]}
    assert service.requests[1].headers == {"If-None-Match": "e1"}


def test_google_transport_needs_a_key_and_hides_it() -> None:
    with pytest.raises(ValueError):
        GoogleTransport("")
    assert "secret-key" not in repr(GoogleTransport("secret-key"))


def test_google_transport_builds_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    import googleapiclient.discovery

    built: list[dict] = []
    monkeypatch.setattr(
        googleapiclient.discovery, "build", lambda *a, **k: built.append(k) or _Service(body={})
    )
    transport = GoogleTransport("k")
    assert built == []
    transport.call("videos", "list", id="a")
    transport.call("videos", "list", id="b")
    assert built == [{"developerKey": "k", "cache_discovery": False}]


# --- dry run -------------------------------------------------------------------------------


def test_dry_run_never_calls_the_network(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    import googleapiclient.discovery

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network touched during a dry run")

    monkeypatch.setattr(googleapiclient.discovery, "build", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)

    transport = DryRunTransport()
    api = DataApi(ledger(conn, run_cap=500, dry_run=True), transport)
    assert api.search("x", order="date", published_after="2026-01-01T00:00:00Z") == {"items": []}
    assert api.channels(["UC1"]) == {"items": []}
    assert list(api.iter_playlist_items("UU1")) == []
    assert api.videos(vid_ids(50)) == {"items": []}

    assert [(c[0], c[3]) for c in transport.calls] == [
        ("search", 100),
        ("channels", 1),
        ("playlistItems", 1),
        ("videos", 1),
    ]
    assert transport.units == 103
    assert api.ledger.run_used == 103
    assert repo.quota_used(conn, DAY) == 0
    assert conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0] == 0


def test_dry_run_reports_where_the_run_cap_would_stop(conn: sqlite3.Connection) -> None:
    api = DataApi(ledger(conn, run_cap=150, dry_run=True), DryRunTransport())
    api.search("x", order="date", published_after="2026-01-01T00:00:00Z")
    with pytest.raises(QuotaExhausted) as info:
        api.search("y", order="date", published_after="2026-01-01T00:00:00Z")
    assert info.value.kind == "run"


# --- parsing -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("PT45S", 45),
        ("PT2M", 120),
        ("PT1H2M3S", 3723),
        ("P0D", 0),
        ("PT1M3S", 63),
        ("P1DT1S", 86401),
    ],
)
def test_parse_duration(value: str, seconds: int) -> None:
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "P", "PT", "1M3S", "PT1.5S", "P1DT"])
def test_parse_duration_refuses_junk(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)


def test_parse_dt() -> None:
    assert parse_dt("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert parse_dt("2026-01-02T04:04:05+01:00") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_dt("2026-01-02T03:04:05")
