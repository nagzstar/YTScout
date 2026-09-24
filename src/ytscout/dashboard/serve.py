"""``ytscout serve``: the localhost review server behind the dashboard buttons (008).

Stdlib ``http.server``, bound to 127.0.0.1 only. It serves the dashboard directory as
static files and takes ``POST /decide`` with ``{"kind", "id", "decision"}``. A decision
is written to the ``decisions`` table, sets the target's ``status``, is appended to
``data/decisions.json`` (JSON Lines, the audit trail for undoing a fat-fingered click by
hand) and rebuilds the dashboard. One person, one machine: no auth, no HTTPS.
"""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

from ytscout.dashboard.build import build
from ytscout.store import connect, now_utc, read_copy, repo

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DECISIONS_FILENAME = "decisions.json"
MAX_BODY_BYTES = 4096


class ReviewServer(HTTPServer):
    """An ``HTTPServer`` that knows where the DB, the dashboard and the audit log live.

    Single-threaded on purpose: one click at a time means one writer at a time.
    """

    def __init__(self, port: int, db_path: Path, out: Path, decisions_path: Path) -> None:
        self.db_path = Path(db_path)
        self.out = Path(out)
        self.decisions_path = Path(decisions_path)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        handler = partial(ReviewHandler, directory=str(self.out.parent))
        super().__init__((HOST, port), handler)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}/"

    def rebuild(self) -> None:
        """Render the dashboard from a read-only copy of the DB."""
        conn = read_copy(self.db_path)
        try:
            build(conn, self.out)
        finally:
            conn.close()

    def decide(self, kind: str, target_id: str, decision: str) -> dict[str, str]:
        """Record one decision; raise ``ValueError``/``LookupError`` and change nothing."""
        entry = {"ts": now_utc(), "kind": kind, "id": target_id, "decision": decision}
        conn = connect(self.db_path)
        try:
            with conn:  # the audit line is written before commit: no row without a line
                repo.record_decision(conn, kind, target_id, decision, entry["ts"])
                self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
                with self.decisions_path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        finally:
            conn.close()
        self.rebuild()
        return entry


class ReviewHandler(SimpleHTTPRequestHandler):
    server: ReviewServer

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path.split("?", 1)[0] == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "db": str(self.server.db_path)})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        if self.path.split("?", 1)[0] != "/decide":
            self._error(HTTPStatus.NOT_FOUND, f"no POST handler for {self.path}")
            return
        # Only the page this server serves may post: a JSON body forces a CORS preflight
        # (which is never answered), and a foreign Origin or Host is refused outright.
        port = self.server.server_address[1]
        local = {f"{HOST}:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in local or (
            origin is not None and origin.removeprefix("http://") not in local
        ):
            self._error(HTTPStatus.FORBIDDEN, "decisions come only from the local dashboard")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send Content-Type: application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY_BYTES:
            self._error(HTTPStatus.BAD_REQUEST, f"body must be 1..{MAX_BODY_BYTES} bytes")
            return
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "body is not JSON")
            return
        if not isinstance(body, dict) or not all(
            isinstance(body.get(k), str) and body.get(k) for k in ("kind", "id", "decision")
        ):
            self._error(HTTPStatus.BAD_REQUEST, 'expected {"kind", "id", "decision"} as strings')
            return
        try:
            self.server.decide(body["kind"], body["id"], body["decision"])
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except LookupError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc.args[0] if exc.args else exc))
            return
        except sqlite3.Error as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"database error: {exc}")
            return
        self._json(HTTPStatus.OK, {"ok": True})

    def end_headers(self) -> None:
        # The page changes on every decision; never let the browser reuse a stale copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(
    db_path: Path, out: Path, decisions_path: Path, port: int = DEFAULT_PORT
) -> ReviewServer:
    """A ready ``ReviewServer`` on 127.0.0.1 (``port=0`` picks a free port)."""
    return ReviewServer(port, db_path, out, decisions_path)
