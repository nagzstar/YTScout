"""Analytics API: OAuth token checks, collect --analytics against a fake transport."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ytscout import cli
from ytscout.cli import EXIT_NO_OAUTH_TOKEN, EXIT_OK, main
from ytscout.collect.analytics import (
    batches,
    collect_analytics,
    rpm_usd,
    window,
)
from ytscout.store import connect, repo
from ytscout.youtube import oauth
from ytscout.youtube.analytics import (
    AnalyticsApi,
    FakeAnalyticsTransport,
    QueryRejected,
    rows_as_dicts,
)
from ytscout.youtube.oauth import ANALYTICS_SCOPE, MONETARY_SCOPE, check_scopes

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANNEL_ID = "UCown000000000000000000"
START, END = "2025-08-20", "2026-09-23"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def creds(*scopes: str) -> SimpleNamespace:
    return SimpleNamespace(scopes=list(scopes), granted_scopes=None)


def table(headers: list[str], rows: list[list[Any]]) -> dict:
    return {"columnHeaders": [{"name": h} for h in headers], "rows": rows}


VIDEO_HEADERS = [
    "video",
    "views",
    "estimatedRevenue",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "subscribersGained",
    "subscribersLost",
]


def video_row(video_id: str, views: int, revenue: float) -> list[Any]:
    return [video_id, views, revenue, 500.0, 21.5, 87.25, 40, 12, 2]


class Handler:
    """Answers the three query kinds; optionally rejects impressions metrics."""

    def __init__(self, *, reject_impressions: bool) -> None:
        self.reject_impressions = reject_impressions

    def __call__(self, params: dict[str, Any]) -> dict:
        assert params["ids"] == "channel==MINE"
        if params["dimensions"] == "video":
            if "impressions" in params["metrics"]:
                if self.reject_impressions:
                    raise QueryRejected("Unknown identifier (impressions) given in field metrics")
                headers = [*VIDEO_HEADERS, "impressions", "impressionsClickThroughRate"]
                ids = params["filters"].removeprefix("video==").split(",")
                return table(headers, [[*video_row(v, 2000, 0.5), 10000, 4.2] for v in ids])
            ids = params["filters"].removeprefix("video==").split(",")
            rows = [video_row(v, 2000, 0.5) for v in ids]
            rows[0] = video_row(ids[0], 0, 0.0)
            return table(VIDEO_HEADERS, rows)
        if params["dimensions"] == "day":
            return table(
                ["day", "views", "estimatedRevenue", "monetizedPlaybacks"],
                [["2026-09-22", 1000, 0.25, 300], ["2026-09-23", 1500, 0.5, 450]],
            )
        if params["dimensions"] == "insightTrafficSourceType":
            return table(
                ["insightTrafficSourceType", "views"], [["SHORTS", 9000], ["YT_SEARCH", 50]]
            )
        raise AssertionError(params)


# --- pure helpers -------------------------------------------------------------------------


def test_rpm_is_revenue_per_thousand_views() -> None:
    assert rpm_usd(0.5, 2000) == pytest.approx(0.25)
    assert rpm_usd(12.0, 48000) == pytest.approx(0.25)
    assert rpm_usd(1.0, 0) is None
    assert rpm_usd(None, 100) is None


def test_window_ends_yesterday_and_is_inclusive() -> None:
    from datetime import date

    assert window(400, date(2026, 9, 24)) == (START, END)
    assert window(1, date(2026, 9, 24)) == (END, END)


def test_batches_of_at_most_200() -> None:
    sizes = [len(b) for b in batches([f"v{i}" for i in range(450)])]
    assert sizes == [200, 200, 50]


def test_rows_as_dicts_keys_by_column_header() -> None:
    assert rows_as_dicts(table(["day", "views"], [["2026-01-01", 3]])) == [
        {"day": "2026-01-01", "views": 3}
    ]
    assert rows_as_dicts({"columnHeaders": [{"name": "day"}]}) == []


# --- scopes -------------------------------------------------------------------------------


def test_check_scopes_ok_for_the_two_readonly_scopes() -> None:
    assert check_scopes(creds(ANALYTICS_SCOPE, MONETARY_SCOPE)) == []


def test_check_scopes_flags_missing_monetary_and_write_scopes() -> None:
    problems = check_scopes(creds(ANALYTICS_SCOPE, UPLOAD_SCOPE))
    assert f"missing scope {MONETARY_SCOPE}" in problems
    assert f"not read-only: {UPLOAD_SCOPE}" in problems
    assert len(problems) == 2


def test_check_scopes_prefers_granted_scopes() -> None:
    token = SimpleNamespace(scopes=[UPLOAD_SCOPE], granted_scopes=[MONETARY_SCOPE])
    assert check_scopes(token) == []


def test_load_credentials_absent_is_none(tmp_path: Path) -> None:
    assert oauth.load_credentials(tmp_path / "token.json") is None
    assert oauth.describe(tmp_path / "token.json").present is False


def test_load_credentials_unreadable_is_token_error(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    path.write_text('{"token": "x"}', encoding="utf-8")
    with pytest.raises(oauth.TokenError):
        oauth.load_credentials(path)
    status = oauth.describe(path)
    assert status.present and not status.loaded and status.error


# --- collector ----------------------------------------------------------------------------


def test_collect_writes_own_analytics_daily_and_traffic() -> None:
    conn = connect(Path(":memory:"))
    transport = FakeAnalyticsTransport(Handler(reject_impressions=False))
    counts = collect_analytics(
        AnalyticsApi(transport), conn, ["vA", "vB", "vC"], start=START, end=END
    )
    assert (counts.videos, counts.days, counts.traffic_sources) == (3, 2, 2)
    assert counts.impressions_available is True
    assert counts.queries == 3

    row = conn.execute("SELECT * FROM own_analytics WHERE video_id = 'vB'").fetchone()
    assert (row["window_start"], row["window_end"]) == (START, END)
    assert row["views"] == 2000
    assert row["rpm_usd"] == pytest.approx(0.25)
    assert row["avg_view_duration_s"] == 21.5
    assert row["avg_view_pct"] == 87.25
    assert row["minutes_watched"] == 500.0
    assert row["likes"] == 40
    assert row["sub_delta"] == 10
    assert row["impressions"] == 10000
    assert row["ctr"] == 4.2
    assert row["collected_at"]

    days = conn.execute("SELECT * FROM own_daily ORDER BY day").fetchall()
    assert [d["day"] for d in days] == ["2026-09-22", "2026-09-23"]
    assert days[1]["monetized_playbacks"] == 450
    traffic = {r["source"]: r["views"] for r in conn.execute("SELECT * FROM own_traffic")}
    assert traffic == {"SHORTS": 9000, "YT_SEARCH": 50}


def test_impressions_rejected_retries_without_and_stores_null() -> None:
    conn = connect(Path(":memory:"))
    transport = FakeAnalyticsTransport(Handler(reject_impressions=True))
    ids = [f"v{i:03d}" for i in range(250)]
    counts = collect_analytics(AnalyticsApi(transport), conn, ids, start=START, end=END)

    video_calls = [c for c in transport.calls if c["dimensions"] == "video"]
    asked = ["impressions" in c["metrics"] for c in video_calls]
    # First batch asks, is rejected, retries without; the second batch never asks.
    assert asked == [True, False, False]
    assert counts.impressions_available is False
    assert counts.videos == 250
    nulls = conn.execute(
        "SELECT COUNT(*) FROM own_analytics WHERE impressions IS NULL AND ctr IS NULL"
    ).fetchone()[0]
    assert nulls == 250
    # A video with no views has no RPM rather than a division by zero.
    zero = conn.execute("SELECT rpm_usd FROM own_analytics WHERE video_id = 'v000'").fetchone()
    assert zero[0] is None


def test_rerun_same_window_replaces_rows() -> None:
    conn = connect(Path(":memory:"))
    for _ in range(2):
        transport = FakeAnalyticsTransport(Handler(reject_impressions=False))
        collect_analytics(AnalyticsApi(transport), conn, ["vA"], start=START, end=END)
    assert conn.execute("SELECT COUNT(*) FROM own_analytics").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM own_daily").fetchone()[0] == 2


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repo root with settings, no .env, and the token path inside it."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(f"own_channel_id: {CHANNEL_ID}\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "config" / "scoring.yaml", config / "scoring.yaml")
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed_own_videos(root: Path, ids: list[str]) -> None:
    conn = connect(root / "data" / "ytscout.sqlite")
    with conn:
        repo.upsert_channel(conn, CHANNEL_ID, role="own", title="Own")
        for vid in ids:
            repo.upsert_video(
                conn,
                vid,
                channel_id=CHANNEL_ID,
                title=vid,
                published_at="2026-01-01T00:00:00Z",
                duration_s=60,
                is_short=True,
            )
    conn.close()


def test_missing_token_exits_4_naming_auth(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--analytics"]) == EXIT_NO_OAUTH_TOKEN
    err = capsys.readouterr().err
    assert "no OAuth token" in err
    assert "ytscout auth" in err
    assert not (repo_root / "data").exists()


def test_token_with_scope_problems_exits_4(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_credentials", lambda path: creds(ANALYTICS_SCOPE, UPLOAD_SCOPE))
    assert main(["collect", "--analytics"]) == EXIT_NO_OAUTH_TOKEN
    err = capsys.readouterr().err
    assert "yt-analytics-monetary.readonly" in err
    assert "youtube.upload" in err
    assert "ytscout auth" in err


def test_collect_analytics_cli_writes_rows_and_prints_no_money(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_own_videos(repo_root, ["vA", "vB"])
    transport = FakeAnalyticsTransport(Handler(reject_impressions=True))
    monkeypatch.setattr(
        cli, "load_credentials", lambda path: creds(ANALYTICS_SCOPE, MONETARY_SCOPE)
    )
    monkeypatch.setattr(cli, "make_analytics_transport", lambda credentials, dry_run: transport)
    assert main(["collect", "--analytics", "--days", "30"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "videos 2, days 2, traffic sources 2" in out
    assert "rejected by the API" in out
    assert "0.25" not in out and "0.5" not in out and "revenue" not in out.lower()

    conn = sqlite3.connect(repo_root / "data" / "ytscout.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM own_analytics").fetchone()[0] == 2
    run = conn.execute("SELECT kind, status FROM runs").fetchone()
    assert run == ("collect_analytics", "ok")
    conn.close()


def test_dry_run_prints_three_queries_without_a_token(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(path: Path) -> None:
        raise AssertionError("a dry run must not load credentials")

    monkeypatch.setattr(cli, "load_credentials", boom)
    assert main(["collect", "--analytics", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.count("reports.query") == 3
    assert "dimensions=video " in out
    assert "dimensions=day " in out
    assert "dimensions=insightTrafficSourceType" in out
    assert not (repo_root / "data").exists()


def test_dry_run_batches_real_video_ids(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_own_videos(repo_root, [f"v{i:03d}" for i in range(201)])
    assert main(["collect", "--analytics", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "filters=video==<200 id(s)>" in out
    assert "filters=video==<1 id(s)>" in out
    assert out.count("reports.query") == 4


def test_auth_status_absent_token(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["auth", "--status"]) == EXIT_OK
    assert "token: absent" in capsys.readouterr().out


def test_auth_status_reports_scope_verdict_without_values(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = repo_root / "scripts" / ".secrets" / "token.json"
    token.parent.mkdir(parents=True)
    token.write_text("{}", encoding="utf-8")
    fake = SimpleNamespace(
        scopes=[ANALYTICS_SCOPE, UPLOAD_SCOPE],
        granted_scopes=None,
        expired=False,
        refresh_token="SECRET-REFRESH",
        token="SECRET-ACCESS",
    )
    monkeypatch.setattr(oauth, "load_credentials", lambda path, refresh=True: fake)
    assert main(["auth", "--status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "token: present" in out
    assert "refresh token: yes" in out
    assert "NOT FIT" in out
    assert f"missing scope {MONETARY_SCOPE}" in out
    assert f"not read-only: {UPLOAD_SCOPE}" in out
    assert "SECRET" not in out
