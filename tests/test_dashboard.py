"""``dashboard``: one self-contained HTML file from a DB seeded by the 004 fixture."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_collect_own import CHANNEL_ID, own_fixtures

from ytscout import cli
from ytscout.cli import EXIT_OK, main
from ytscout.collect import collect_own
from ytscout.dashboard import build, load
from ytscout.store import connect, read_copy, repo
from ytscout.youtube import DataApi, FakeTransport, Ledger

# Far from the real clock, so the ledger's own charges (on the real Pacific day) never
# land on the days the header reads. 12:00 UTC is 04:00 Pacific: "today" is 2030-01-02.
NOW = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)
FIXTURE_TITLES = [f"Top 5 fixture #{i}" for i in range(1, 6)]
MAX_BYTES = 5 * 1024 * 1024

# Any attribute or CSS reference that points off the machine.
EXTERNAL_REF = re.compile(
    r"""(?:\b(?:src|href|action|poster|data)\s*=\s*["']?|url\(\s*["']?)(https?:|//)""", re.I
)
ALLOWED_HREF = re.compile(r"""href="https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]+\"""")


def _seed(conn: sqlite3.Connection) -> None:
    ledger = Ledger(conn, 9000, run_cap=50)
    counts = collect_own(
        DataApi(ledger, FakeTransport(own_fixtures())),
        conn,
        CHANNEL_ID,
        videos=200,
        shorts_max_seconds=180,
    )
    assert counts.stopped is None
    with conn:
        repo.add_quota_used(conn, "2030-01-02", 321)
        repo.add_quota_used(conn, "2030-01-01", 1234)
        repo.upsert_channel(conn, "UCcand1", role="competitor", title="Wild Tops <5>")
        repo.set_channel_discovery(
            conn,
            "UCcand1",
            {"score": 7, "hit_count": 3, "reasons": ["subs 5,000 within [120, 12,000]"]},
        )
        repo.add_channel_snapshot(conn, "UCcand1", subs=5000, view_count=1, video_count=1)
        repo.upsert_channel(conn, "UCcand2", role="competitor", title="Low Score Beasts")
        repo.set_channel_discovery(conn, "UCcand2", {"score": 2, "hit_count": 1, "reasons": []})
        repo.upsert_channel(conn, "UCappr", role="competitor", title="Approved Animals")
        repo.set_channel_status(conn, "UCappr", "approved")
        repo.upsert_channel(conn, "UCrej", role="competitor", title="Rejected Rivals")
        repo.set_channel_status(conn, "UCrej", "rejected")
        run_id = repo.start_run(conn, "collect_own")
        repo.finish_run(conn, run_id, "ok")


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "ytscout.sqlite"
    conn = connect(path)
    try:
        _seed(conn)
    finally:
        conn.close()
    return path


def _build(db_path: Path, out: Path) -> str:
    conn = read_copy(db_path)
    try:
        build(conn, out, now=NOW)
    finally:
        conn.close()
    return out.read_text(encoding="utf-8")


def assert_self_contained(html: str) -> None:
    assert 'src="http' not in html
    assert re.search(r"""href=["']?https?://[^"'\s>]*\.css""", html) is None
    assert "@import" not in html
    assert 'fetch("http' not in html
    offenders = [
        html[m.start() : m.start() + 80]
        for m in EXTERNAL_REF.finditer(html)
        if not ALLOWED_HREF.match(html, m.start())
    ]
    assert offenders == []


def test_build_from_seeded_db(seeded: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "index.html"
    html = _build(seeded, out)
    assert out.is_file()
    assert out.stat().st_size < MAX_BYTES
    assert "Countdown Animal Kingdom (fixture)" in html
    for title in FIXTURE_TITLES:
        assert title in html
    assert html.count('href="https://www.youtube.com/watch?v=own0000000') == 5
    assert_self_contained(html)


def test_chartjs_is_inlined_without_a_source_map(seeded: Path, tmp_path: Path) -> None:
    html = _build(seeded, tmp_path / "index.html")
    assert "Chart.js v4" in html
    assert 'id="subs-chart"' in html
    assert 'id="subs-data"' in html
    assert "sourceMappingURL" not in html
    assert "<script src" not in html
    assert "<link" not in html


def test_header_quota_and_last_run(seeded: Path) -> None:
    conn = read_copy(seeded)
    try:
        dash = load(conn, now=NOW)
    finally:
        conn.close()
    assert dash.own is not None and dash.own["title"] == "Countdown Animal Kingdom (fixture)"
    assert dash.quota_today == 321
    assert dash.quota_yesterday == 1234
    assert dash.last_run is not None
    assert (dash.last_run["kind"], dash.last_run["status"]) == ("collect_own", "ok")
    assert len(dash.own_videos) == 5
    assert len(dash.subs_series) == 1


def test_candidates_sorted_by_score_and_approved_split(seeded: Path, tmp_path: Path) -> None:
    conn = read_copy(seeded)
    try:
        dash = load(conn, now=NOW)
    finally:
        conn.close()
    assert [c["id"] for c in dash.candidates] == ["UCcand1", "UCcand2"]
    top = dash.candidates[0]
    assert (top["subs"], top["hit_count"], top["score"]) == (5000, 3, 7)
    assert top["reasons"] == ["subs 5,000 within [120, 12,000]"]
    assert [c["id"] for c in dash.approved] == ["UCappr"]

    html = _build(seeded, tmp_path / "index.html")
    assert "Wild Tops &lt;5&gt;" in html  # escaped, not raw markup
    assert "Rejected Rivals" not in html
    # 008: 3 live buttons x 2 candidate rows, each carrying what POST /decide needs.
    buttons = re.findall(r"<button [^>]*>", html)
    assert len(buttons) == 6
    for tag in buttons:
        assert 'data-kind="channel"' in tag
        assert re.search(r'data-id="UCcand[12]"', tag)
        assert re.search(r'data-decision="(approved|rejected|watch)"', tag)
        assert "disabled" not in tag
    assert 'data-id="UCcand1" data-decision="approved"' in html
    assert 'id="serve-note"' in html
    assert "http://127.0.0.1:8765/" in html


def test_empty_db_renders_nothing_yet(tmp_path: Path) -> None:
    html = _build(tmp_path / "missing.sqlite", tmp_path / "index.html")
    assert not (tmp_path / "missing.sqlite").exists()
    assert html.count("Nothing yet") >= 6  # own, candidates, approved, niches, findings, runs
    assert 'id="subs-data"' not in html
    assert_self_contained(html)


def test_the_whitelist_catches_a_cdn(seeded: Path, tmp_path: Path) -> None:
    html = _build(seeded, tmp_path / "index.html")
    for bad in (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/x.css">',
        "<style>@import url(https://example.com/a.css);</style>",
        '<a href="https://www.youtube.com.evil.example/">x</a>',
    ):
        with pytest.raises(AssertionError):
            assert_self_contained(html.replace("</body>", bad + "</body>"))


def test_read_copy_leaves_the_real_db_untouched(seeded: Path, tmp_path: Path) -> None:
    before = seeded.read_bytes()
    _build(seeded, tmp_path / "index.html")
    assert seeded.read_bytes() == before


def test_cli_writes_default_out_under_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(f"own_channel_id: {CHANNEL_ID}\n", encoding="utf-8")
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    conn = connect(tmp_path / "data" / "ytscout.sqlite")
    try:
        _seed(conn)
    finally:
        conn.close()

    assert main(["dashboard"]) == EXIT_OK
    out = tmp_path / "dashboard" / "index.html"
    assert "Top 5 fixture #3" in out.read_text(encoding="utf-8")
    assert "5 own videos, 2 candidates, 1 approved" in capsys.readouterr().out

    custom = tmp_path / "elsewhere" / "d.html"
    assert main(["dashboard", "--out", str(custom)]) == EXIT_OK
    assert custom.is_file()


def test_cli_with_no_db_and_no_settings_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_repo_root", lambda: tmp_path)
    assert main(["dashboard"]) == EXIT_OK
    assert (tmp_path / "dashboard" / "index.html").is_file()
    assert not (tmp_path / "data").exists()
