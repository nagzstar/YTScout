"""``serve``: POST /decide in-process on an ephemeral 127.0.0.1 port (never `ytscout serve`)."""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ytscout.dashboard.serve import HOST, ReviewServer, make_server
from ytscout.store import connect, repo

SERVE_PY = Path(__file__).parents[1] / "src" / "ytscout" / "dashboard" / "serve.py"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "ytscout.sqlite"
    conn = connect(path)
    try:
        with conn:
            for cid in ("UCa", "UCb", "UCc"):
                repo.upsert_channel(conn, cid, role="competitor", title=f"Channel {cid}")
            conn.execute(
                "INSERT INTO niches (id, format, topic, created_at) VALUES (7, 'shorts', 'x', 't')"
            )
    finally:
        conn.close()
    return path


@pytest.fixture
def server(db_path: Path, tmp_path: Path) -> Iterator[ReviewServer]:
    srv = make_server(
        db_path,
        tmp_path / "dashboard" / "index.html",
        tmp_path / "data" / "decisions.json",
        port=0,
    )
    srv.rebuild()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def request(
    srv: ReviewServer,
    method: str,
    path: str,
    body: object = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(HOST, srv.server_address[1], timeout=10)
    try:
        data = None if body is None else json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json"} if data is not None else {}
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, json.loads(raw) if raw else {}
    finally:
        conn.close()


def statuses(db_path: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT id, status FROM channels ORDER BY id").fetchall())
    finally:
        conn.close()


def test_binds_only_to_loopback(server: ReviewServer) -> None:
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    assert port != 0
    assert server.url == f"http://127.0.0.1:{port}/"
    assert "0.0.0.0" not in SERVE_PY.read_text(encoding="utf-8")


def test_health(server: ReviewServer, db_path: Path) -> None:
    assert request(server, "GET", "/health") == (200, {"ok": True, "db": str(db_path)})


def test_serves_the_dashboard(server: ReviewServer) -> None:
    conn = http.client.HTTPConnection(HOST, server.server_address[1], timeout=10)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        html = resp.read().decode("utf-8")
    finally:
        conn.close()
    assert resp.status == 200
    assert 'data-id="UCa" data-decision="approved"' in html


def test_three_decisions_land_everywhere(
    server: ReviewServer, db_path: Path, tmp_path: Path
) -> None:
    index = tmp_path / "dashboard" / "index.html"
    before = index.stat().st_mtime_ns
    os.utime(index, ns=(before - 10**9, before - 10**9))  # so a rebuild visibly advances it
    before = index.stat().st_mtime_ns

    clicks = [("UCa", "approved"), ("UCb", "rejected"), ("UCc", "watch")]
    for cid, decision in clicks:
        body = {"kind": "channel", "id": cid, "decision": decision}
        assert request(server, "POST", "/decide", body) == (200, {"ok": True})

    assert statuses(db_path) == {"UCa": "approved", "UCb": "rejected", "UCc": "watch"}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT kind, target_id, decision FROM decisions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("channel", cid, d) for cid, d in clicks]

    lines = (tmp_path / "data" / "decisions.json").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [(e["kind"], e["id"], e["decision"]) for e in entries] == [
        ("channel", cid, d) for cid, d in clicks
    ]
    assert all(set(e) == {"ts", "kind", "id", "decision"} and e["ts"] for e in entries)

    assert index.stat().st_mtime_ns > before
    html = index.read_text(encoding="utf-8")
    assert 'data-id="UCa"' not in html  # decided rows leave the candidates table
    assert "Channel UCa" in html and "Channel UCc" in html  # approved + watch listed
    assert "Channel UCb" not in html  # rejected hidden


def test_niche_decision_sets_niche_status(server: ReviewServer, db_path: Path) -> None:
    body = {"kind": "niche", "id": "7", "decision": "track"}
    assert request(server, "POST", "/decide", body) == (200, {"ok": True})
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT status FROM niches WHERE id = 7").fetchone() == ("track",)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"kind": "channel", "id": "UCa", "decision": "maybe"}, 400),
        ({"kind": "channel", "id": "UCa", "decision": "track"}, 400),
        ({"kind": "playlist", "id": "UCa", "decision": "approved"}, 400),
        ({"kind": "niche", "id": "seven", "decision": "track"}, 400),
        ({"kind": "channel", "id": "UCa"}, 400),
        (["channel", "UCa", "approved"], 400),
        ({"kind": "channel", "id": "UCnope", "decision": "approved"}, 404),
    ],
)
def test_bad_decisions_change_nothing(
    server: ReviewServer, db_path: Path, tmp_path: Path, body: object, status: int
) -> None:
    code, payload = request(server, "POST", "/decide", body)
    assert code == status
    assert payload["ok"] is False and payload["error"]
    assert statuses(db_path) == {"UCa": None, "UCb": None, "UCc": None}
    assert not (tmp_path / "data" / "decisions.json").exists()


def test_foreign_origin_and_non_json_are_refused(server: ReviewServer, db_path: Path) -> None:
    body = {"kind": "channel", "id": "UCa", "decision": "approved"}
    code, _ = request(server, "POST", "/decide", body, {"Origin": "https://evil.example"})
    assert code == 403
    code, _ = request(server, "POST", "/decide", body, {"Host": "evil.example:8765"})
    assert code == 403
    code, _ = request(server, "POST", "/decide", body, {"Content-Type": "text/plain"})
    assert code == 415
    assert statuses(db_path)["UCa"] is None


def test_cli_prints_url_and_stops_cleanly_on_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ytscout import cli

    def interrupted(self: ReviewServer, poll_interval: float = 0.5) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(ReviewServer, "serve_forever", interrupted)
    assert cli.main(["serve", "--port", "0"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "open http://127.0.0.1:" in out and "serve: stopped" in out
    assert (tmp_path / "dashboard" / "index.html").is_file()
    assert not (tmp_path / "data").exists()  # starting up reads the DB, never creates it
