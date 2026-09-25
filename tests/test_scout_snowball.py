"""``scout snowball`` (026): known channels' top titles → searches → clusters → proposals.

The fixture DB has two sources: an approved competitor ``COMP1`` (animals, 4 videos, the
top 3 used) and ``TRK1``, the one channel of a ``track`` niche (history_science). Their 6
titles give 6 queries. The searches return:

* cluster A — ``A1``..``A4``, 3 hits each on the snake/shark queries, 9 of 12 videos ≤ 180 s
  (75 % ≥ 70 %) → a Shorts niche ``deadliest-snakes`` (animals_nature);
* cluster B — ``B1``..``B3``, 3 hits each on the history queries, 2 of 9 videos short
  → a long-form niche ``ancient-empires`` (history_science);
* noise — ``N1`` (3 cooking hits, a cluster of one), ``N2`` (2 snake hits: dropped by the
  3-hit rule before it could join A), and known channels ``COMP1`` (tracked) and ``KN1``
  (in another niche's ``niche_channels``), which would otherwise join the clusters.

Hand-computed cost: 6 searches × 100 + 1 channels.list (8 channels) + 1 videos.list
(21 hit videos in the two clusters) = 602 units.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ytscout import cli, text
from ytscout.cli import EXIT_ERROR, EXIT_OK, EXIT_QUOTA_EXHAUSTED, main
from ytscout.scout import snowball as sb
from ytscout.store import connect, repo
from ytscout.youtube import DataApi, Ledger

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
FIXTURE_UNITS = 6 * 100 + 1 + 1

Q_EMPIRES = "top 5 ancient empires"
Q_SNAKES = "top 5 deadliest snakes"
Q_ROMAN = "ancient roman"
Q_SHARKS = "top 5 biggest sharks"
Q_CITIES = "lost cities"
Q_VENOM = "snake venomous"
EXPECTED_QUERIES = [Q_EMPIRES, Q_SNAKES, Q_ROMAN, Q_SHARKS, Q_CITIES, Q_VENOM]

# Source videos: (video id, title, views).
COMP1_VIDEOS = [
    ("c1", "Top 5 Deadliest Snakes in the World", 900),
    ("c2", "Top 5 Biggest Sharks Ever Caught #shorts", 800),
    ("c3", "Snake vs Snake: Venomous Snake Battle", 700),
    ("c4", "Cute Cats Compilation", 100),
]
TRK1_VIDEOS = [
    ("t1", "Top 5 Ancient Empires That Vanished", 5000),
    ("t2", "Ancient Roman Legion Secrets", 4000),
    ("t3", "10 Lost Cities of the Ancient World", 3000),
]

# query -> [(channel, video id, title, duration seconds)]
SEARCHES: dict[str, list[tuple[str, str, str, int]]] = {
    Q_EMPIRES: [
        ("B1", "b11", "Ancient Empires Lost Forever", 900),
        ("B2", "b21", "Ancient Empires Explained", 900),
        ("B3", "b31", "Ancient Empires Documentary", 900),
        ("N1", "n11", "Easy Pasta Recipe", 40),
        ("KN1", "k11", "Ancient Empires Ranked", 900),
        ("TRK1", "t1", "Top 5 Ancient Empires That Vanished", 50),
    ],
    Q_SNAKES: [
        ("A1", "a11", "Deadliest Snakes Ranked", 40),
        ("A2", "a21", "Deadliest Snakes Alive", 40),
        ("A3", "a31", "Deadliest Snakes Explained", 40),
        ("A4", "a41", "Deadliest Snakes Bites", 40),
        ("N2", "n21", "Deadliest Snakes Ranked Again", 40),
        ("COMP1", "c1", "Top 5 Deadliest Snakes in the World", 50),
    ],
    Q_ROMAN: [
        ("B1", "b12", "Ancient Roman Legions", 900),
        ("B2", "b22", "Ancient Roman Empires", 900),
        ("B3", "b32", "Roman Legions Documentary", 60),
        ("N1", "n12", "Quick Pasta Dinner", 40),
        ("KN1", "k12", "Ancient Roman Empires Ranked", 900),
    ],
    Q_SHARKS: [
        ("A1", "a12", "Deadliest Sharks Ranked", 40),
        ("A2", "a22", "Deadliest Sharks Alive", 600),
        ("A3", "a32", "Deadliest Sharks Explained", 600),
        ("A4", "a42", "Deadliest Sharks Attacks", 600),
        ("COMP1", "c2", "Top 5 Biggest Sharks Ever Caught #shorts", 50),
    ],
    Q_CITIES: [
        ("B1", "b13", "Lost Cities of Ancient Empires", 900),
        ("B2", "b23", "Lost Cities Explained", 900),
        ("B3", "b33", "Lost Ancient Empires", 60),
        ("N1", "n13", "Pasta Sauce Secrets", 40),
        ("KN1", "k13", "Lost Ancient Empires Ranked", 900),
    ],
    Q_VENOM: [
        ("A1", "a13", "Venomous Snakes Ranked", 40),
        ("A2", "a23", "Venomous Snakes Alive", 40),
        ("A3", "a33", "Venomous Snakes Explained", 40),
        ("A4", "a43", "Deadliest Venomous Bites", 40),
        ("N2", "n22", "Venomous Snakes Ranked Again", 40),
        ("COMP1", "c3", "Snake vs Snake: Venomous Snake Battle", 50),
    ],
}
NEW_CHANNELS = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "N1", "N2"]


def _iso_seconds(seconds: int) -> str:
    return f"PT{seconds // 60}M{seconds % 60}S"


class FixtureTransport:
    """Searches by ``q``; channels by id (every new channel exists); videos by id."""

    def __init__(self) -> None:
        self.durations = {v: d for rows in SEARCHES.values() for _c, v, _t, d in rows}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        self.calls.append((resource, params))
        if resource == "search":
            return {
                "items": [
                    {"id": {"videoId": v}, "snippet": {"channelId": c, "title": t}}
                    for c, v, t, _d in SEARCHES.get(params["q"], [])
                ]
            }
        ids = params["id"].split(",")
        if resource == "channels":
            return {"items": [{"id": i, "snippet": {"title": i}} for i in ids]}
        return {
            "items": [
                {"id": i, "contentDetails": {"duration": _iso_seconds(self.durations[i])}}
                for i in ids
                if i in self.durations
            ]
        }

    def count(self, resource: str) -> int:
        return sum(1 for r, _ in self.calls if r == resource)


def _channel(
    conn: sqlite3.Connection, channel_id: str, role: str, status: str | None = None
) -> None:
    repo.upsert_channel(conn, channel_id, role=role, title=channel_id)
    if status is not None:
        repo.set_channel_status(conn, channel_id, status)


def _videos(conn: sqlite3.Connection, channel_id: str, videos: list[tuple[str, str, int]]) -> None:
    for video_id, title, views in videos:
        repo.upsert_video(conn, video_id, channel_id=channel_id, title=title)
        repo.add_video_snapshot(conn, video_id, views=views, likes=None, comments=None)


def _niche(conn: sqlite3.Connection, topic: str, category: str | None, queries: list[str]) -> int:
    return repo.insert_niche(
        conn,
        fmt="shorts",
        topic=topic,
        topic_category=category,  # type: ignore[arg-type]
        label=topic,
        source="seed",
        queries=queries,
        required_steps=[],
        created_at="2026-09-01T00:00:00Z",
    )


def seed(conn: sqlite3.Connection, *, track_category: str | None = "history_science") -> None:
    with conn:
        _channel(conn, "COMP1", role="competitor", status="approved")
        _videos(conn, "COMP1", COMP1_VIDEOS)
        _channel(conn, "REJ1", role="competitor", status="rejected")
        _videos(conn, "REJ1", [("r1", "Top 5 Rejected Things", 10**9)])
        _channel(conn, "TRK1", role="niche_sample")
        _videos(conn, "TRK1", TRK1_VIDEOS)
        _channel(conn, "KN1", role="niche_sample")
        _videos(conn, "KN1", [("k0", "Top 5 Known Channel Titles", 10**9)])
        tracked = _niche(conn, "history-countdowns", track_category, ["history countdown"])
        repo.set_niche_status(conn, tracked, "track")
        repo.put_niche_channel(conn, tracked, "TRK1", is_small=False, added_at=NOW)
        other = _niche(conn, "roman-history", "history_science", ["roman history"])
        repo.mark_niche_validated(conn, other, NOW)
        repo.put_niche_channel(conn, other, "KN1", is_small=False, added_at=NOW)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "db.sqlite")
    seed(c)
    yield c
    c.close()


def api_for(conn: sqlite3.Connection, transport: Any, run_cap: int | None = None) -> DataApi:
    return DataApi(Ledger(conn, 9000, run_cap=run_cap, clock=lambda: NOW), transport)


def run(conn: sqlite3.Connection, api: DataApi) -> sb.SnowballResult:
    queries = sb.plan_queries(conn, sb.DEFAULT_MAX_SEARCHES)
    return sb.snowball(
        api, conn, queries, lookback_days=365, language="en", shorts_max_seconds=180, now=NOW
    )


def snowball_niches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM niches WHERE source = 'snowball' ORDER BY id").fetchall()


# --- text.snowball_query --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Top 5 Deadliest Snakes in the World", "top 5 deadliest snakes"),
        ("Snake vs Snake: Venomous Snake Battle", "snake venomous"),  # frequency first
        ("10 Lost Cities of the Ancient World", "lost cities"),  # numerals stripped, no top 5
        ("TOP FIVE Weird Fish", "top 5 weird fish"),
        ("Top5 Owls", "top 5 owls"),  # one content word is enough
        ("Top 5 #shorts #viral", None),  # nothing left
    ],
)
def test_snowball_query(title: str, expected: str | None) -> None:
    assert text.snowball_query(title) == expected


# --- planning -------------------------------------------------------------------------------


def test_queries_come_round_robin_from_both_sources(conn: sqlite3.Connection) -> None:
    planned = sb.plan_queries(conn, 10)
    assert [p.query for p in planned] == EXPECTED_QUERIES
    by_query = {p.query: p for p in planned}
    assert by_query[Q_SNAKES].category == "animals_nature"
    assert by_query[Q_SNAKES].source_channel == "COMP1"
    assert by_query[Q_EMPIRES].category == "history_science"
    # the 4th competitor video, the rejected competitor and the untracked niche's channel
    # are not sources
    assert not {p.source_video for p in planned} & {"c4", "r1", "k0"}


def test_queries_drop_existing_niche_queries_and_cap(conn: sqlite3.Connection) -> None:
    with conn:
        _niche(conn, "used", None, ["  Top 5 DEADLIEST snakes "])
    planned = [p.query for p in sb.plan_queries(conn, 10)]
    assert Q_SNAKES not in planned and len(planned) == 5
    assert [p.query for p in sb.plan_queries(conn, 2)] == [Q_EMPIRES, Q_ROMAN]


def test_worst_case_units() -> None:
    # 10 searches: ⌊500/3⌋ = 166 channels → 4 calls; 500 videos → 10 calls.
    assert sb.worst_case_units(10) == 1000 + 4 + 10
    assert sb.worst_case_units(15) == 1500 + 5 + 15
    assert sb.worst_case_units(0) == 0
    assert sb.searches_within(1013, 10) == 9
    assert sb.searches_within(1014, 10) == 10
    assert sb.searches_within(101, 10) == 0


# --- clustering (pure) ----------------------------------------------------------------------


def test_cluster_single_linkage_and_minimum_size() -> None:
    words = {
        "a": {"x", "y", "z"},
        "b": {"y", "z", "w"},  # J(a, b) = 2/4
        "c": {"w", "v", "u", "t"},  # J(b, c) = 1/6 < 0.3 ...
        "d": {"w", "v", "u"},  # ... but J(c, d) = 3/4 and J(b, d) = 1/5
        "e": {"z", "w", "v"},  # J(b, e) = 2/4, J(d, e) = 2/4: chains a..d together
        "f": {"q"},
    }
    assert sb.cluster(words) == [["a", "b", "c", "d", "e"]]
    assert sb.cluster(words, min_size=1)[-1] == ["f"]


def test_jaccard_exact_threshold_links() -> None:
    a, b = {"p", "q", "r"}, {"r", "s", "t", "p", "u", "v", "w"}  # 2 / 8 = 0.25
    assert sb.jaccard(a, b) == 0.25
    c = {"p", "q", "r", "s", "t", "u", "v", "w", "x", "y"}
    assert sb.jaccard({"p", "q", "r"}, c) == pytest.approx(0.3)
    assert sb.cluster({"1": {"p", "q", "r"}, "2": c, "3": c}, min_size=3) == [["1", "2", "3"]]


# --- the run --------------------------------------------------------------------------------


def test_run_proposes_exactly_the_two_clusters(conn: sqlite3.Connection) -> None:
    transport = FixtureTransport()
    result = run(conn, api_for(conn, transport))

    assert result.stopped is None
    assert result.searches == 6
    assert result.candidate_channels == len(NEW_CHANNELS)
    assert result.kept_channels == 8  # N2 has 2 hits
    assert result.clusters == 2
    rows = snowball_niches(conn)
    assert [(r["format"], r["topic"]) for r in rows] == [
        ("shorts", "deadliest-snakes"),
        ("longform", "ancient-empires"),
    ]
    shorts, longform = rows
    assert shorts["label"] == "Deadliest snakes"
    assert shorts["topic_category"] == "animals_nature"
    assert shorts["status"] == "proposed"
    assert json.loads(shorts["queries_json"]) == [Q_SNAKES, Q_SHARKS, Q_VENOM]
    assert json.loads(shorts["seed_json"]) == ["A1", "A2", "A3", "A4"]
    meta = json.loads(shorts["meta_json"])
    assert meta["shorts_share"] == 0.75 and meta["category_fallback"] is False
    assert meta["hit_count"] == 12
    assert longform["topic_category"] == "history_science"
    assert json.loads(longform["queries_json"]) == [Q_EMPIRES, Q_ROMAN, Q_CITIES]
    assert json.loads(longform["seed_json"]) == ["B1", "B2", "B3"]
    assert json.loads(longform["meta_json"])["shorts_share"] == pytest.approx(2 / 9, abs=1e-4)


def test_unit_total_matches_hand_count(conn: sqlite3.Connection) -> None:
    transport = FixtureTransport()
    api = api_for(conn, transport)
    result = run(conn, api)
    assert result.units == FIXTURE_UNITS == api.ledger.run_used
    assert transport.count("search") == 6
    assert transport.count("channels") == 1
    assert transport.count("videos") == 1
    channel_ids = set(next(p for r, p in transport.calls if r == "channels")["id"].split(","))
    assert channel_ids == {"A1", "A2", "A3", "A4", "B1", "B2", "B3", "N1"}
    video_ids = next(p for r, p in transport.calls if r == "videos")["id"].split(",")
    assert len(video_ids) == 21 and not any(v.startswith("n") for v in video_ids)


def test_search_parameters(conn: sqlite3.Connection) -> None:
    transport = FixtureTransport()
    run(conn, api_for(conn, transport))
    params = [p for r, p in transport.calls if r == "search"]
    assert [p["q"] for p in params] == EXPECTED_QUERIES
    assert {p["order"] for p in params} == {"viewCount"}
    assert {p["publishedAfter"] for p in params} == {"2025-09-25T12:00:00Z"}
    assert all("videoDuration" not in p for p in params)


def test_known_channels_are_excluded(conn: sqlite3.Connection) -> None:
    run(conn, api_for(conn, FixtureTransport()))
    seeds = {c for r in snowball_niches(conn) for c in json.loads(r["seed_json"])}
    assert not seeds & {"COMP1", "TRK1", "KN1", "N1", "N2"}
    # nothing but niches is written: no channel rows for the new channels
    assert repo.get_channel(conn, "A1") is None


def test_rerun_has_no_new_queries(conn: sqlite3.Connection) -> None:
    run(conn, api_for(conn, FixtureTransport()))
    # all 6 source queries are now example queries of the two new niches
    assert [p.query for p in sb.plan_queries(conn, 10)] == []


def test_existing_topic_is_skipped(conn: sqlite3.Connection) -> None:
    with conn:
        _niche(conn, "deadliest-snakes", "animals_nature", ["snakes"])
    result = run(conn, api_for(conn, FixtureTransport()))
    assert [p.topic for _id, p in result.added] == ["ancient-empires"]
    assert [(p.topic, why) for p, why in result.skipped] == [
        ("deadliest-snakes", "niche already exists")
    ]


def test_category_fallback_is_flagged(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    seed(conn, track_category=None)
    run(conn, api_for(conn, FixtureTransport()))
    longform = snowball_niches(conn)[1]
    assert longform["topic_category"] == "entertainment_pop"
    assert json.loads(longform["meta_json"])["category_fallback"] is True
    conn.close()


def test_quota_stop_writes_nothing(conn: sqlite3.Connection) -> None:
    result = run(conn, api_for(conn, FixtureTransport(), run_cap=250))
    assert result.stopped is not None
    assert result.searches == 2
    assert snowball_niches(conn) == []


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, FixtureTransport]:
    """A repo root with settings.yaml, the real scoring.yaml and a seeded DB."""
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
    seed(c)
    c.close()
    transport = FixtureTransport()
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: transport)
    return tmp_path, transport


def test_cli_refuses_more_than_15_searches(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scout", "snowball", "--max-searches", "16", "--dry-run"]) == EXIT_ERROR
    assert "above 15" in capsys.readouterr().err


def test_cli_requires_a_quota_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scout", "snowball"]) == EXIT_ERROR
    assert "--max-units" in capsys.readouterr().err


def test_cli_dry_run_prints_queries_and_estimate(
    cli_env: tuple[Path, FixtureTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    root, transport = cli_env
    assert main(["scout", "snowball", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    for query in EXPECTED_QUERIES:
        assert repr(query) in out
    assert f"estimate <= {sb.worst_case_units(6)} units" in out
    assert transport.calls == []


def test_cli_run_and_unit_cap(
    cli_env: tuple[Path, FixtureTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    _root, transport = cli_env
    assert main(["scout", "snowball", "--max-units", "50"]) == EXIT_QUOTA_EXHAUSTED
    assert transport.calls == []
    assert main(["scout", "snowball", "--max-units", "1000"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "deadliest-snakes" in out and "ancient-empires" in out
    assert f"units this run {FIXTURE_UNITS}" in out
