"""``dashboard``: one self-contained HTML file from a DB seeded by the 004 fixture."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_collect_own import CHANNEL_ID, own_fixtures
from test_scoring import CFG, COVERAGE
from test_scout_score import NOW as SCORED_AT
from test_scout_score import add_niche, build_example, repo_root, run_score  # noqa: F401

from ytscout import cli
from ytscout.cli import EXIT_OK, main
from ytscout.collect import collect_own
from ytscout.dashboard import NicheContext, build, load, render
from ytscout.dashboard.build import _sig2, trend
from ytscout.store import connect, read_copy, repo, to_utc_iso
from ytscout.youtube import DataApi, FakeTransport, Ledger

# Far from the real clock, so the ledger's own charges (on the real Pacific day) never
# land on the days the header reads. 12:00 UTC is 04:00 Pacific: "today" is 2030-01-02.
NOW = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)
FIXTURE_TITLES = [f"Top 5 fixture #{i}" for i in range(1, 6)]
MAX_BYTES = 5 * 1024 * 1024
CONTEXT = NicheContext(CFG, COVERAGE)

# Any attribute or CSS reference that points off the machine.
EXTERNAL_REF = re.compile(
    r"""(?:\b(?:src|href|action|poster|data)\s*=\s*["']?|url\(\s*["']?)(https?:|//)""", re.I
)
ALLOWED_HREF = re.compile(
    r"""href="https://www\.youtube\.com/(watch\?v=|channel/)[A-Za-z0-9_-]+\""""
)


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
    for cid in ("UCcand1", "UCcand2", "UCappr", "UCrej"):  # 035: rejected stays listed
        assert f'href="https://www.youtube.com/channel/{cid}"' in html
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
    # 035: rejected channels stay on the page, in a collapsed table.
    assert re.search(r'<details id="rejected">\s*<summary>Rejected \(1\)</summary>', html)
    assert "Rejected Rivals" in html
    # 008: 3 live buttons x 2 candidate rows, each carrying what POST /decide needs.
    buttons = re.findall(r'<button [^>]*data-id="UCcand[12]"[^>]*>', html)
    assert len(buttons) == 6
    for tag in buttons:
        assert 'data-kind="channel"' in tag
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
        '<a href="https://www.youtube.com/@handle">x</a>',
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


# ---------------------------------------------------------------- niches (025)


def _seed_niches(conn: sqlite3.Connection) -> dict[str, int]:
    """023's example scored for real, plus hand-written rows: an older example score (for
    the trend), a higher-scoring long-form niche (tracking), a disqualified Shorts niche
    (shelved) and a proposed one (waiting)."""
    example = build_example(conn)
    with conn:
        repo.add_niche_score(
            conn,
            example,
            scored_at=SCORED_AT - timedelta(days=30),
            flags=[],
            opportunity=0.30,
            score=0.04,
        )
    run_score(conn)
    with conn:
        deep = add_niche(conn, topic="deep-sea", fmt="longform", status="track")
        repo.add_niche_score(
            conn,
            deep,
            scored_at=SCORED_AT,
            flags=["longform_uncalibrated", "low_confidence"],
            opportunity=0.5,
            newcomer_monthly_views_p25=1000,
            newcomer_monthly_views_p50=2000,
            newcomer_monthly_views_p75=4000,
            rpm_gbp=3.9,
            est_monthly_gbp=7.8,
            manual_hours_per_month=5.2,
            score=1.5,
        )
        cars = add_niche(conn, topic="car-crashes", status="shelve")
        repo.add_niche_score(
            conn,
            cars,
            scored_at=SCORED_AT,
            flags=["disqualified", "uncalibrated"],
            opportunity=0.2,
            est_monthly_gbp=0.5,
            manual_hours_per_month=None,
            score=0.0,
        )
        waiting = add_niche(conn, topic="volcano-facts", status="proposed", tagged=False)
    return {"example": example, "deep": deep, "cars": cars, "waiting": waiting}


@pytest.fixture
def niche_db(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    path = tmp_path / "data" / "ytscout.sqlite"
    conn = connect(path)
    try:
        ids = _seed_niches(conn)
    finally:
        conn.close()
    return path, ids


def test_sig2() -> None:
    assert [_sig2(v) for v in (0.0578, 1.04, 18.0, 13_300, 9.96, 0, None, 0.387)] == [
        "0.058",
        "1.0",
        "18",
        "13,000",
        "10",
        "0",
        "–",
        "0.39",
    ]


def test_trend_arrow_needs_a_row_28_days_older() -> None:
    def row(days_ago: int, opp: float | None) -> dict:
        return {"scored_at": to_utc_iso(SCORED_AT - timedelta(days=days_ago)), "opportunity": opp}

    assert trend([row(0, 0.5)]) == "—"
    assert trend([row(27, 0.1), row(0, 0.5)]) == "—"
    assert trend([row(28, 0.45), row(0, 0.5)]) == "▲"  # exactly +0.05
    assert trend([row(28, 0.55), row(0, 0.5)]) == "▼"
    assert trend([row(60, 0.1), row(29, 0.48), row(1, 0.9), row(0, 0.5)]) == "▬"


def test_niches_load_ranked_per_format(niche_db: tuple[Path, dict[str, int]]) -> None:
    path, ids = niche_db
    conn = read_copy(path)
    try:
        dash = load(conn, now=NOW, context=CONTEXT)
    finally:
        conn.close()
    shorts, longform = dash.niches["shorts"], dash.niches["longform"]
    assert [n["id"] for n in shorts] == [ids["example"], ids["cars"]]
    assert [n["id"] for n in longform] == [ids["deep"]]
    top = shorts[0]
    assert round(top["score"], 4) == 0.0578  # the latest row, not the older 0.04
    assert top["trend"] == "▲"  # 0.387 against 0.30, 30 days older
    assert round(top["est_p25"], 3) == 0.585 and round(top["est_p75"], 2) == 2.08
    assert longform[0]["trend"] == "—"
    assert [n["id"] for n in dash.niches_waiting] == [ids["waiting"]]
    assert dash.niche_counts == {"scored": 3, "tracking": 1, "shelved": 1}
    sample = top["sample"]
    assert sample["queries"] == ["q1", "q2", "q3"]
    assert {"id": "qa", "coverage": "manual"} in sample["steps"]
    assert sample["channels"], "the 023 example has small channels"
    assert any(c["outliers"] for c in sample["channels"])
    for c in sample["channels"]:
        for v in c["outliers"]:
            assert v["id"].startswith(c["id"] + "-v")


def test_niches_render(niche_db: tuple[Path, dict[str, int]], tmp_path: Path) -> None:
    path, ids = niche_db
    conn = read_copy(path)
    try:
        build(conn, tmp_path / "index.html", now=NOW, context=CONTEXT)
    finally:
        conn.close()
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "3 niches scored · 1 tracking · 1 shelved" in html

    def first_row(fmt: str) -> str:
        panel = html.split(f'data-niche-format-panel="{fmt}"', 1)[1]
        found = re.search(r'<tr class="niche-row" data-niche-id="(\d+)"', panel)
        assert found is not None
        return found.group(1)

    assert first_row("shorts") == str(ids["example"])
    assert first_row("longform") == str(ids["deep"])
    # Shorts is the default; long-form starts hidden.
    assert 'data-niche-format-panel="shorts">' in html
    assert 'data-niche-format-panel="longform" hidden>' in html
    assert "<strong>0.058</strong>" in html and 'title="0.0577778"' in html
    assert "£1.0 (£0.59–£2.1)" in html
    for flag in ("low_confidence", "uncalibrated", "disqualified"):
        assert f'<span class="badge badge-{flag}">{flag}</span>' in html
    assert "∞" in html  # disqualified: NULL hours
    decisions = re.findall(r'<button [^>]*data-kind="niche"[^>]*>', html)
    assert len(decisions) == 6
    for nid in (ids["example"], ids["deep"], ids["cars"]):
        assert f'data-id="{nid}" data-decision="track"' in html
        assert f'data-id="{nid}" data-decision="shelve"' in html
    assert 'id="niche-min-opp" value="0"' in html
    sample = html.split(f'id="niche-sample-{ids["example"]}"', 1)[1].split("</tr>", 1)[0]
    assert 'href="https://www.youtube.com/watch?v=UC' in sample
    assert 'href="https://www.youtube.com/channel/UC' in sample
    assert "qa: " in sample and "cov-manual" in sample
    waiting = html.split("Waiting (1)", 1)[1]
    assert "Top 5 countdown: volcano-facts" in waiting and "proposed" in waiting
    assert_self_contained(html)


def test_niches_render_without_config(niche_db: tuple[Path, dict[str, int]]) -> None:
    path, _ids = niche_db
    conn = read_copy(path)
    try:
        dash = load(conn, now=NOW)
    finally:
        conn.close()
    html = render(dash)
    assert dash.niches["shorts"][0]["sample"]["channels"] is None
    assert "did not load" in html and "coverage unknown" in html


@pytest.mark.usefixtures("repo_root")
def test_cli_dashboard_renders_niches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conn = connect(tmp_path / "data" / "ytscout.sqlite")
    try:
        ids = _seed_niches(conn)
    finally:
        conn.close()
    assert main(["dashboard"]) == EXIT_OK
    assert "3 niches scored" in capsys.readouterr().out
    html = (tmp_path / "dashboard" / "index.html").read_text(encoding="utf-8")
    sample = html.split(f'id="niche-sample-{ids["example"]}"', 1)[1].split("</tr>", 1)[0]
    assert 'href="https://www.youtube.com/watch?v=UC' in sample  # config/ read from the root
    assert "cov-manual" in sample
    assert_self_contained(html)
