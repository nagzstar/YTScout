"""035: undo a decision, keep decided channels visible, and bulk-decide from the CLI."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from ytscout import cli
from ytscout.cli import EXIT_ERROR, EXIT_OK, main
from ytscout.dashboard import build
from ytscout.store import connect, read_copy, repo

STATUSES = {"UCnull": None, "UCappr": "approved", "UCrej": "rejected", "UCwatch": "watch"}


def _seed(path: Path) -> None:
    conn = connect(path)
    try:
        with conn:
            for cid, status in STATUSES.items():
                repo.upsert_channel(conn, cid, role="competitor", title=f"T {cid}")
                if status:
                    repo.set_channel_status(conn, cid, status)
            repo.add_channel_snapshot(conn, "UCnull", subs=500, view_count=1, video_count=1)
            repo.upsert_channel(conn, "UCsmall", role="competitor", title="Small")
            repo.add_channel_snapshot(conn, "UCsmall", subs=9000, view_count=1, video_count=1)
            repo.upsert_channel(conn, "UCbig", role="competitor", title="Big")
            repo.add_channel_snapshot(conn, "UCbig", subs=50_000, view_count=1, video_count=1)
            repo.upsert_channel(conn, "UCown", role="own", title="Own")
    finally:
        conn.close()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_repo_root", lambda: tmp_path)
    _seed(tmp_path / "data" / "ytscout.sqlite")
    return tmp_path


def _db(root: Path) -> Path:
    return root / "data" / "ytscout.sqlite"


def _statuses(root: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(_db(root))
    try:
        return dict(conn.execute("SELECT id, status FROM channels").fetchall())
    finally:
        conn.close()


def _decisions(root: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(_db(root))
    try:
        return conn.execute("SELECT target_id, decision FROM decisions ORDER BY id").fetchall()
    finally:
        conn.close()


def _audit(root: Path) -> list[dict]:
    path = root / "data" / "decisions.json"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_row_carries_three_buttons_current_one_disabled(root: Path) -> None:
    conn = read_copy(_db(root))
    try:
        build(conn, root / "index.html")
    finally:
        conn.close()
    html = (root / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<details id="rejected">\s*<summary>Rejected \(1\)</summary>', html)

    for cid, status in STATUSES.items():
        rows = re.findall(rf'<tr data-channel-id="{cid}".*?</tr>', html, re.DOTALL)
        assert len(rows) == 1, cid
        row = rows[0]
        assert f"<td>{status or 'candidate'}</td>" in row
        buttons = re.findall(r'<button [^>]*data-decision="(?:approved|rejected|watch)"[^>]*>', row)
        assert len(buttons) == 3, cid
        disabled = [b for b in buttons if " disabled" in b]
        undo = re.findall(r'<button [^>]*data-decision="undecided"[^>]*>', row)
        if status is None:
            assert disabled == [] and undo == []  # a candidate has no current decision
        else:
            assert len(disabled) == 1, cid
            assert f'data-decision="{status}"' in disabled[0]
            assert len(undo) == 1 and " disabled" not in undo[0]


def test_record_decision_undecided_clears_status(root: Path) -> None:
    conn = connect(_db(root))
    try:
        with conn:
            repo.record_decision(conn, "channel", "UCappr", "undecided", "2030-01-01T00:00:00Z")
    finally:
        conn.close()
    assert _statuses(root)["UCappr"] is None
    assert _decisions(root) == [("UCappr", "undecided")]


def test_decide_where_dry_run_lists_and_writes_nothing(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _db(root).read_bytes()
    assert main(["decide", "--where", "status is null", "--set", "rejected", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for cid in ("UCnull", "UCsmall", "UCbig"):
        assert f"would set  {cid}" in out
    assert "UCown" not in out and "UCappr" not in out
    assert "3 of 3 rows would change, nothing written" in out
    assert _db(root).read_bytes() == before
    assert _audit(root) == []


def test_decide_where_writes_one_row_and_one_line_per_channel(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["decide", "--where", "status is null", "--set", "rejected"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "set  UCbig  Big  subs=50,000  undecided -> rejected" in out
    changed = ["UCbig", "UCsmall", "UCnull"]  # ordered by title: Big, Small, T UCnull
    assert _decisions(root) == [(cid, "rejected") for cid in changed]
    audit = _audit(root)
    assert [(e["id"], e["decision"], e["via"]) for e in audit] == [
        (cid, "rejected", "cli") for cid in changed
    ]
    assert all(_statuses(root)[cid] == "rejected" for cid in changed)


def test_decide_where_on_subs(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    where = "subs < 10000 and status is null"
    assert main(["decide", "--where", where, "--set", "rejected"]) == EXIT_OK
    assert _decisions(root) == [("UCsmall", "rejected"), ("UCnull", "rejected")]


def test_decide_channel_undecided_and_unchanged(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["decide", "--channel", "UCappr", "--channel", "UCnull", "--set", "undecided"]
    assert main(argv) == EXIT_OK
    out = capsys.readouterr().out
    assert "approved -> undecided" in out
    assert "unchanged  UCnull" in out
    assert _decisions(root) == [("UCappr", "undecided")]
    assert _statuses(root)["UCappr"] is None


def test_decide_unknown_channel_writes_nothing(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["decide", "--channel", "UCappr", "--channel", "UCnope", "--set", "rejected"]
    assert main(argv) == EXIT_ERROR
    assert "no competitor UCnope" in capsys.readouterr().err
    assert _decisions(root) == []


@pytest.mark.parametrize(
    "where",
    [
        "(DELETE FROM channels)",
        "status is null; DROP TABLE channels",
        "no_such_column = 1",
    ],
)
def test_decide_where_cannot_write(
    root: Path, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _statuses(root)
    assert main(["decide", "--where", where, "--set", "rejected"]) == EXIT_ERROR
    assert "bad --where" in capsys.readouterr().err
    assert _statuses(root) == before
    assert _decisions(root) == []
