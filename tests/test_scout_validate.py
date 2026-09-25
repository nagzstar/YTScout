"""``scout validate`` (022): searches → channels → one uploads page + videos per channel.

The fixture niche (``tests/fixtures/validate/``) has 6 searches over 12 distinct channels:
4 small and 8 big by the rule, with the edge cases (small by subs but old, young but big,
exactly at the subs cap, exactly 365 days old). ``UC02`` has 35 uploads (30 are fetched)
and ``UC11`` an empty uploads playlist (no videos call). Hand-computed cost:

    6 searches × 100 + 1 channels.list + 12 playlistItems.list + 11 videos.list = 624
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ytscout import cli
from ytscout.cli import EXIT_ERROR, EXIT_OK, EXIT_QUOTA_EXHAUSTED, main
from ytscout.scoring import ValidationConfig, load_scoring, validation_config
from ytscout.scout import validate as v
from ytscout.store import connect, repo
from ytscout.youtube import DataApi, Ledger, QuotaExhausted

FIXTURES = Path(__file__).parent / "fixtures" / "validate"
REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
FIXTURE_UNITS = 6 * 100 + 1 + 12 + 11
CFG = ValidationConfig(
    small_subs_max=10_000,
    small_age_days=365,
    lookback_days=365,
    max_channels=80,
    early_stop_after_searches=4,
    videos_per_channel=30,
    language="en",
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureTransport:
    """Answers by resource: searches by (q, order), channels / videos by id, playlists by id.

    An unknown search query gets an empty page, so a second niche can run on the same
    transport.
    """

    def __init__(self) -> None:
        self.searches = {
            (s["q"], s["order"]): {"items": s["items"]}
            for s in (load(f"search_{i}.json") for i in range(1, 7))
        }
        self.channels = {c["id"]: c for c in load("channels.json")["items"]}
        self.playlists = load("playlists.json")
        self.videos = {item["id"]: item for item in load("videos.json")["items"]}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        self.calls.append((resource, params))
        if resource == "search":
            return self.searches.get((params["q"], params["order"]), {"items": []})
        if resource == "channels":
            ids = params["id"].split(",")
            return {"items": [self.channels[i] for i in ids if i in self.channels]}
        if resource == "playlistItems":
            return self.playlists[params["playlistId"]]
        ids = params["id"].split(",")
        return {"items": [self.videos[i] for i in ids if i in self.videos]}

    def count(self, resource: str) -> int:
        return sum(1 for r, _ in self.calls if r == resource)


def add_niche(
    conn: sqlite3.Connection, *, topic: str = "", created_at: str = "2026-09-01T00:00:00Z"
) -> int:
    spec = load("niche.json")
    with conn:
        return repo.insert_niche(
            conn,
            fmt=spec["format"],
            topic=topic or spec["topic"],
            topic_category=spec["topic_category"],
            label=spec["label"],
            source="seed",
            queries=spec["queries"] if not topic else [f"{topic} a", f"{topic} b"],
            required_steps=[],
            created_at=created_at,
        )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "db.sqlite")
    yield c
    c.close()


def api_for(conn: sqlite3.Connection, transport: Any, run_cap: int | None = None) -> DataApi:
    return DataApi(Ledger(conn, 9000, run_cap=run_cap, clock=lambda: NOW), transport)


def run(conn: sqlite3.Connection, api: DataApi, niches: list[Any]) -> v.ValidateResult:
    return v.validate_niches(api, conn, niches, cfg=CFG, shorts_max_seconds=180, now=NOW)


# --- pure helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subs", "created", "expected"),
    [
        (5_000, datetime(2026, 3, 1, tzinfo=UTC), True),  # few subs, young
        (3_000, datetime(2021, 1, 1, tzinfo=UTC), False),  # few subs, old
        (50_000, datetime(2026, 5, 1, tzinfo=UTC), False),  # many subs, young
        (500_000, datetime(2019, 4, 1, tzinfo=UTC), False),  # many subs, old
        (10_000, datetime(2026, 7, 1, tzinfo=UTC), False),  # at the cap is not under it
        (9_999, datetime(2025, 9, 25, 12, tzinfo=UTC), False),  # exactly 365 days old
        (None, datetime(2026, 3, 1, tzinfo=UTC), None),  # hidden subscriber count
        (5_000, None, None),  # no creation date
    ],
)
def test_is_small(subs: int | None, created: datetime | None, expected: bool | None) -> None:
    assert v.is_small(subs, created, NOW, CFG) is expected


def test_rank_channels_caps_by_hit_count_keeping_first_seen_order_on_ties() -> None:
    hits = {"a": 1, "b": 3, "c": 1, "d": 2}
    assert v.rank_channels(hits, 3) == ["b", "d", "a"]


def test_worst_case_is_the_issue_budget() -> None:
    assert v.worst_case_units(CFG) == 600 + 2 + 160 == 762
    assert v.worst_case_units(CFG, 4) == 562


def test_planned_searches_are_query_major_with_format_duration() -> None:
    niche = {"id": 1, "format": "longform", "queries_json": json.dumps(["a", "b", "c", "d"])}
    specs = v.planned_searches(niche)
    assert [(s.query, s.order) for s in specs] == [
        ("a", "viewCount"),
        ("a", "date"),
        ("b", "viewCount"),
        ("b", "date"),
        ("c", "viewCount"),
        ("c", "date"),
    ]
    assert {s.video_duration for s in specs} == {"medium"}


def test_config_section_matches_the_issue() -> None:
    assert validation_config(load_scoring(REPO_ROOT / "config" / "scoring.yaml")) == CFG


# --- the fixture niche ----------------------------------------------------------------------


def test_fixture_niche_end_to_end(conn: sqlite3.Connection) -> None:
    with conn:
        repo.upsert_channel(conn, "UC07", role="competitor", title="Tracked")
        repo.set_channel_status(conn, "UC07", "approved")
    niche_id = add_niche(conn)
    transport = FixtureTransport()
    api = api_for(conn, transport)
    result = run(conn, api, repo.proposed_niches(conn))

    assert result.stopped is None
    (report,) = result.reports
    assert api.ledger.run_used == report.units == FIXTURE_UNITS == 624
    assert repo.quota_used(conn, api.ledger.today()) == 624
    assert (report.searches, report.distinct_channels, report.sampled, report.small) == (
        6,
        12,
        12,
        4,
    )
    assert report.videos == 10 * 3 + 30
    assert [transport.count(r) for r in ("search", "channels", "playlistItems", "videos")] == [
        6,
        1,
        12,
        11,
    ]

    first = transport.calls[0][1]
    assert first["videoDuration"] == "short"
    assert first["maxResults"] == 50
    assert first["publishedAfter"] == "2025-09-25T12:00:00Z"
    assert first["relevanceLanguage"] == "en"
    (uc02_videos,) = [p for r, p in transport.calls if r == "videos" and "V0200" in p["id"]]
    assert len(uc02_videos["id"].split(",")) == 30

    small = {r["channel_id"] for r in repo.niche_channels(conn, niche_id) if r["is_small"]}
    assert small == {"UC01", "UC05", "UC09", "UC12"}
    assert len(repo.niche_channels(conn, niche_id)) == 12
    niche = repo.get_niche(conn, niche_id)
    assert niche["status"] == "validated"
    assert niche["validated_at"] == "2026-09-25T12:00:00Z"

    tracked = repo.get_channel(conn, "UC07")
    assert (tracked["role"], tracked["status"]) == ("competitor", "approved")
    assert repo.get_channel(conn, "UC01")["role"] == "niche_sample"
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 60
    assert conn.execute("SELECT COUNT(*) FROM video_snapshots").fetchone()[0] == 60
    assert conn.execute("SELECT COUNT(*) FROM channel_snapshots").fetchone()[0] == 12


def test_revalidation_appends_snapshots_and_keeps_links(conn: sqlite3.Connection) -> None:
    niche_id = add_niche(conn)
    run(conn, api_for(conn, FixtureTransport()), repo.proposed_niches(conn))
    run(conn, api_for(conn, FixtureTransport()), [repo.get_niche(conn, niche_id)])
    assert len(repo.niche_channels(conn, niche_id)) == 12
    assert conn.execute("SELECT COUNT(*) FROM channel_snapshots").fetchone()[0] == 24


# --- search early stop and channel cap ------------------------------------------------------


class ManyChannels:
    """Each search returns 25 channels never seen before; channels.list finds none."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next = 0

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        self.calls.append((resource, params))
        if resource != "search":
            return {"items": []}
        items = [{"snippet": {"channelId": f"UCx{self.next + i:03d}"}} for i in range(25)]
        self.next += 25
        return {"items": items}


def test_search_stops_after_four_when_they_found_the_cap(conn: sqlite3.Connection) -> None:
    add_niche(conn)
    transport = ManyChannels()
    api = api_for(conn, transport)
    (report,) = run(conn, api, repo.proposed_niches(conn)).reports
    assert report.searches == 4 and report.early_stop
    assert report.distinct_channels == 100
    channel_calls = [p["id"].split(",") for r, p in transport.calls if r == "channels"]
    assert [len(ids) for ids in channel_calls] == [50, 30]  # capped at 80
    assert api.ledger.run_used == 4 * 100 + 2


def test_no_early_stop_under_the_cap(conn: sqlite3.Connection) -> None:
    add_niche(conn)
    transport = ManyChannels()
    cfg = ValidationConfig(**{**CFG.__dict__, "max_channels": 101})
    (report,) = v.validate_niches(
        api_for(conn, transport),
        conn,
        repo.proposed_niches(conn),
        cfg=cfg,
        shorts_max_seconds=180,
        now=NOW,
    ).reports
    assert report.searches == 6 and not report.early_stop


def test_channel_cap_prefers_channels_with_more_hits(conn: sqlite3.Connection) -> None:
    add_niche(conn)
    cfg = ValidationConfig(**{**CFG.__dict__, "max_channels": 5})
    transport = FixtureTransport()
    v.validate_niches(
        api_for(conn, transport),
        conn,
        repo.proposed_niches(conn),
        cfg=cfg,
        shorts_max_seconds=180,
        now=NOW,
    )
    (ids,) = [p["id"] for r, p in transport.calls if r == "channels"]
    # UC01 and UC02 have 3 hits, UC03 2; then first-seen order among the single hits.
    assert ids.split(",") == ["UC01", "UC02", "UC03", "UC04", "UC05"]


# --- stops ----------------------------------------------------------------------------------


class GoogleRefusesAfter(Ledger):
    """A ledger whose charges succeed ``n`` times, then Google says quotaExceeded."""

    def __init__(self, conn: sqlite3.Connection, n: int) -> None:
        super().__init__(conn, 9000, clock=lambda: NOW)
        self.left = n

    def charge(self, units: int) -> None:
        if self.left == 0:
            raise QuotaExhausted("google")
        self.left -= 1
        super().charge(units)


def test_all_proposed_marks_the_finished_niche_and_leaves_the_interrupted_one(
    conn: sqlite3.Connection,
) -> None:
    first = add_niche(conn, created_at="2026-09-01T00:00:00Z")
    second = add_niche(conn, topic="second niche", created_at="2026-09-02T00:00:00Z")
    fixture_calls = 6 + 1 + 12 + 11
    ledger = GoogleRefusesAfter(conn, fixture_calls + 1)  # one search into the second niche
    result = run(conn, DataApi(ledger, FixtureTransport()), repo.proposed_niches(conn))

    assert result.stopped is not None and result.stopped.kind == "google"
    assert [r.validated for r in result.reports] == [True, False]
    assert result.reports[1].units == 100
    assert repo.get_niche(conn, first)["status"] == "validated"
    interrupted = repo.get_niche(conn, second)
    assert (interrupted["status"], interrupted["validated_at"]) == ("proposed", None)
    assert ledger.run_used == FIXTURE_UNITS + 100


def test_a_niche_is_not_started_without_room_for_its_worst_case(
    conn: sqlite3.Connection,
) -> None:
    add_niche(conn, created_at="2026-09-01T00:00:00Z")
    second = add_niche(conn, topic="second niche", created_at="2026-09-02T00:00:00Z")
    api = api_for(conn, FixtureTransport(), run_cap=1000)
    result = run(conn, api, repo.proposed_niches(conn))
    # 624 spent leaves 376 < 562 (the second niche has 2 queries → 4 searches).
    assert result.stopped is not None and result.stopped.kind == "run"
    assert result.not_started is not None and result.not_started.niche_id == second
    assert api.ledger.run_used == FIXTURE_UNITS
    assert repo.get_niche(conn, second)["status"] == "proposed"


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text("own_channel_id: UCown\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "config" / "scoring.yaml", config / "scoring.yaml")
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)
    c = connect(tmp_path / "data" / "ytscout.sqlite")
    add_niche(c)
    c.close()
    return tmp_path


def db(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(root / "data" / "ytscout.sqlite")


def test_cli_validates_and_prints_the_actual_units(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: FixtureTransport())
    assert main(["scout", "validate", "--all-proposed", "--max-units", "1000"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "validated; 6 searches, 12 distinct channels, 12 sampled (4 small)" in out
    assert "60 videos; 624 units" in out
    assert "1 of 1 niche(s) validated; units this run 624" in out
    c = db(repo_root)
    assert c.execute("SELECT status FROM niches").fetchone()[0] == "validated"
    assert c.execute("SELECT kind, status FROM runs").fetchall() == [("scout_validate", "ok")]


def test_cli_exit_3_when_the_budget_cannot_cover_a_niche(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FixtureTransport()
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: transport)
    code = main(["scout", "validate", "--niche", "1", "--max-units", "500"])
    assert code == EXIT_QUOTA_EXHAUSTED
    assert "needs up to 762 units" in capsys.readouterr().err
    assert transport.calls == []
    assert db(repo_root).execute("SELECT status FROM niches").fetchone()[0] == "proposed"


def test_cli_dry_run_plans_and_writes_nothing(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_transport(settings: Any, dry_run: bool) -> None:
        raise AssertionError("a dry run needs no transport")

    monkeypatch.setattr(cli, "make_transport", no_transport)
    assert main(["scout", "validate", "--all-proposed", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "niche 1 [shorts] Fixture: ocean countdowns: estimate <= 762 units" in out
    assert out.count("search.list q=") == 6
    assert "videoDuration=short" in out
    c = db(repo_root)
    assert c.execute("SELECT status FROM niches").fetchone()[0] == "proposed"
    assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM quota_ledger").fetchone()[0] == 0


def test_cli_requires_max_units_or_dry_run(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scout", "validate", "--all-proposed"]) == EXIT_ERROR
    assert "--max-units" in capsys.readouterr().err


def test_cli_refuses_a_decided_niche(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    c = connect(repo_root / "data" / "ytscout.sqlite")
    with c:
        repo.set_niche_status(c, 1, "track")
    c.close()
    assert main(["scout", "validate", "--niche", "1", "--dry-run"]) == EXIT_ERROR
    assert "never undone" in capsys.readouterr().err


def test_cli_needs_a_target() -> None:
    with pytest.raises(SystemExit):
        main(["scout", "validate", "--dry-run"])
