"""``discover``: seed queries, the similarity filter, units and idempotence, on fixtures."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ytscout import cli
from ytscout.cli import EXIT_ERROR, EXIT_OK, main
from ytscout.collect import discover as discover_mod
from ytscout.collect.discover import (
    Candidate,
    Verdict,
    discover,
    queries_within,
    screen_channel,
    screen_language,
    screen_views,
    subs_band,
    worst_case_units,
)
from ytscout.scoring import DiscoveryConfig, ScoringConfigError, discovery_config, load_scoring
from ytscout.store import connect, repo
from ytscout.text import STOP_WORDS, parse_keywords, query_terms, seed_queries, tokens
from ytscout.youtube import DataApi, FakeTransport, Ledger

FIXTURES = Path(__file__).parent / "fixtures" / "discover"
REPO_ROOT = Path(__file__).resolve().parents[1]
OWN = "UCownchannel00000000001"
PARTS = "snippet,statistics,contentDetails"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
PUBLISHED_AFTER = "2025-09-24T12:00:00Z"
CANDIDATES = ["UCgood1", "UCsmall1", "UClong", "UCrejected", "UCgood2", "UCsmall2"]
HIT_VIDEOS = [
    "good1vid001",
    "good1vid002",
    "long0vid001",
    "long0vid002",
    "good2vid001",
    "good2vid002",
]
CFG = DiscoveryConfig(
    subs_min=10000,
    subs_max=0,
    hit_views_min=100000,
    shorts_share_min=0.7,
    keyword_overlap_min=2,
    topic_words=("animal", "animals", "snake", "snakes", "wildlife"),
    language="en",
    latin_share_min=0.9,
)


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


TITLES: list[str] = _load("own_titles")


def fixtures() -> dict:
    key = FakeTransport.key

    def search(order: str):
        return key(
            "search",
            "list",
            part="snippet",
            type="video",
            q="top 5 dangerous animals",
            order=order,
            publishedAfter=PUBLISHED_AFTER,
            relevanceLanguage="en",
            maxResults=50,
        )

    return {
        key("channels", "list", part="statistics,brandingSettings", id=OWN, maxResults=50): _load(
            "own_channel"
        ),
        search("viewCount"): _load("search_viewcount"),
        search("date"): _load("search_date"),
        key("channels", "list", part=PARTS, id=",".join(CANDIDATES), maxResults=50): _load(
            "channels_batch"
        ),
        key("videos", "list", part=PARTS, id=",".join(HIT_VIDEOS), maxResults=50): _load(
            "videos_batch"
        ),
    }


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discover_mod, "utc_now", lambda: NOW)


def seed(conn: sqlite3.Connection) -> None:
    """The own channel with its 10 titles and 1,200 subs, and one channel rejected before."""
    with conn:
        repo.upsert_channel(conn, OWN, role="own", title="Countdown Animal Kingdom")
        repo.add_channel_snapshot(conn, OWN, subs=1200, view_count=250000, video_count=10)
        for i, title in enumerate(TITLES):
            repo.upsert_video(
                conn,
                f"own{i:08d}",
                channel_id=OWN,
                title=title,
                published_at=f"2026-09-{20 - i:02d}T11:00:00Z",
            )
        repo.upsert_channel(conn, "UCrejected", role="competitor", title="Seeded title")
        repo.set_channel_status(conn, "UCrejected", "rejected")


def run(conn: sqlite3.Connection, transport: FakeTransport, max_queries: int = 1):
    ledger = Ledger(conn, 9000, run_cap=300)
    result = discover(
        DataApi(ledger, transport),
        conn,
        OWN,
        titles=repo.recent_titles(conn, OWN, 50),
        rejected=repo.channel_ids_with_status(conn, "rejected"),
        max_queries=max_queries,
        cfg=CFG,
        shorts_max_seconds=180,
    )
    return result, ledger


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "x.sqlite")
    seed(c)
    return c


def count(conn: sqlite3.Connection, table: str, where: str = "1") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


# --- text ---------------------------------------------------------------------------------


def test_stop_word_list_has_100_words() -> None:
    assert len(STOP_WORDS) == 100


def test_tokens_strip_numerals_format_tokens_and_stop_words() -> None:
    assert tokens("Top 5 Most DEADLY Snakes #shorts") == ["deadly", "snakes"]
    assert tokens("Countdown: FIVE sharks in 2026, shorts") == ["sharks"]
    assert tokens("You&#39;ve never seen these") == ["you've", "never", "seen"]


def test_tokens_strip_hype_and_format_words() -> None:
    # 034: the own titles carry #Viral and #Versus; neither is a topic.
    assert tokens("Top 5 Viral Animals vs Humans #shorts") == ["animals", "humans"]
    assert tokens("Lion vs Spotted Hyena: Who Hits Harder? #Shorts #Viral #Versus") == [
        "lion",
        "spotted",
        "hyena",
        "hits",
        "harder",
    ]
    assert tokens("Viral video compilation, trending clips, short") == []


def test_seed_queries_never_emit_top_5_alone_or_a_hype_word() -> None:
    titles = [
        "Top 5 Viral Versus #Shorts #Viral #Versus",
        "Top 5 Viral vs #Shorts #Viral #Versus",
        "Top 5 Land Animals #Shorts #Viral #Top5",
        "Top 5 Heaviest Land Animals #Shorts #Viral #Top5",
    ]
    assert seed_queries(titles, ["viral", "shorts", "top 5", "vs"], 10) == [
        "top 5 land animals",
        "top 5 land",
        "top 5 animals",
    ]
    assert query_terms(seed_queries(titles, ["viral"], 10)) == {"land", "animals"}


def test_parse_keywords_keeps_quoted_phrases() -> None:
    assert parse_keywords('animals "wild animals" shorts') == ["animals", "wild animals", "shorts"]
    assert parse_keywords("") == []
    assert parse_keywords(None) == []
    assert parse_keywords('broken "quote') == ["broken", '"quote']


def test_seed_queries_from_the_ten_titles() -> None:
    keywords = parse_keywords(
        _load("own_channel")["items"][0]["brandingSettings"]["channel"]["keywords"]
    )
    assert seed_queries(TITLES, keywords, 10) == [
        # 2-grams in >= 2 titles, most frequent first, ties by first sighting
        "top 5 dangerous animals",
        "top 5 animals world",
        "top 5 deadliest snakes",
        "top 5 venomous snakes",
        # the top 4 1-grams: animals 5, snakes 4, dangerous 3, deadliest 3
        "top 5 animals",
        "top 5 snakes",
        "top 5 dangerous",
        "top 5 deadliest",
        # channel tags; "shorts" has no content word left and is skipped
        "animals",
        "wild animals",
    ]


def test_seed_queries_cap_dedupe_and_no_tags() -> None:
    assert seed_queries(TITLES, [], 3) == [
        "top 5 dangerous animals",
        "top 5 animals world",
        "top 5 deadliest snakes",
    ]
    queries = seed_queries(TITLES, ["top 5 dangerous animals"], 50)
    assert queries.count("top 5 dangerous animals") == 1
    assert len(seed_queries(TITLES, [], 50)) == 8


def test_seed_queries_ignore_singletons() -> None:
    assert seed_queries(["Top 5 Lions", "Top 5 Tigers"], [], 10) == []


def test_query_terms_drop_the_prefix() -> None:
    assert query_terms(["top 5 dangerous animals", "wild animals"]) == {
        "dangerous",
        "animals",
        "wild",
    }


# --- filter -------------------------------------------------------------------------------


def test_subs_band_is_absolute_and_uncapped_by_default() -> None:
    assert subs_band(CFG) == (10000, float("inf"))
    capped = replace(CFG, subs_max=5_000_000)
    assert subs_band(capped) == (10000, 5_000_000)


def test_screen_views_needs_one_viral_hit() -> None:
    v = screen_views(Verdict("UCx", kept=True, reasons=[]), [40_000, 120_000], cfg=CFG)
    assert v.kept and v.reasons == ["top hit 120,000 views >= 100,000"]
    v = screen_views(Verdict("UCx", kept=True, reasons=[]), [40_000, 99_999], cfg=CFG)
    assert not v.kept and v.reasons == ["top hit 99,999 views < 100,000"]
    v = screen_views(Verdict("UCx", kept=True, reasons=[]), [], cfg=CFG)
    assert not v.kept and v.reasons == ["top hit 0 views < 100,000"]


def test_discovery_config_is_read_from_scoring_yaml() -> None:
    real = discovery_config(load_scoring(REPO_ROOT / "config" / "scoring.yaml"))
    # 009: the shipped threshold is 1 (two content words across the seeds make 2 mean
    # "all of them"); the fixture config keeps 2 so the "< 2" reasons stay exercised.
    assert real.keyword_overlap_min == 1
    assert replace(real, topic_words=CFG.topic_words, keyword_overlap_min=2) == CFG
    assert {"animal", "animals", "wildlife"} <= set(real.topic_words)


def test_discovery_config_rejects_bad_words() -> None:
    good = load_scoring(REPO_ROOT / "config" / "scoring.yaml")
    for key, bad in (("topic_words", []), ("topic_words", "animals"), ("language", "")):
        broken = {**good, "discovery": {**good["discovery"], key: bad}}
        with pytest.raises(ScoringConfigError):
            discovery_config(broken)


def test_off_topic_channel_is_dropped_before_videos_list() -> None:
    c = Candidate(
        "UCx",
        video_ids=["v1", "v2"],
        hit_titles=["Top 5 Viral Moments vs Reality", "Top 5 Viral vs Fails"],
        item={"statistics": {"subscriberCount": "500000"}, "snippet": {"title": "Clips"}},
    )
    v = screen_channel(
        c,
        own_channel_id=OWN,
        rejected=set(),
        band=subs_band(CFG),
        terms={"moments", "reality"},
        cfg=CFG,
    )
    assert not v.kept
    assert "no topic word in channel title, description or hit titles" in v.reasons
    c.item["snippet"]["description"] = "Wildlife clips every day"
    assert screen_channel(
        c,
        own_channel_id=OWN,
        rejected=set(),
        band=subs_band(CFG),
        terms={"moments", "reality"},
        cfg=CFG,
    ).kept


def test_hype_words_alone_never_satisfy_keyword_overlap() -> None:
    # 034: a gaming channel whose hits share only "viral" and "vs" with the queries.
    c = Candidate(
        "UCx",
        video_ids=["v1", "v2"],
        hit_titles=["Top 5 Viral Gaming Moments vs Pros", "Viral vs Fails #shorts"],
        item={"statistics": {"subscriberCount": "500000"}, "snippet": {"title": "Animals"}},
    )
    terms = query_terms(["top 5 viral", "top 5 vs", "top 5 viral versus", "top 5 snakes"])
    assert terms == {"snakes"}
    v = screen_channel(
        c, own_channel_id=OWN, rejected=set(), band=subs_band(CFG), terms=terms, cfg=CFG
    )
    assert not v.kept
    assert "keyword overlap 0 < 2 (-)" in v.reasons


def test_screen_language_uses_tags_then_script() -> None:
    def fresh() -> Verdict:
        return Verdict("UCx", kept=True, reasons=[])

    v = screen_language(fresh(), ["en", "en", "hi"], ["x"], cfg=CFG)
    assert v.kept and v.reasons == ["language en (2 of 3 tagged)"]
    v = screen_language(fresh(), ["hi", "hi", "en"], ["x"], cfg=CFG)
    assert not v.kept and v.reasons == ["language hi != en (2 of 3 tagged)"]
    v = screen_language(fresh(), [], ["Top 5 Deadliest Snakes", "Dangerous Animals"], cfg=CFG)
    assert v.kept and v.reasons == ["no language tag; titles 100% latin >= 90%"]
    v = screen_language(fresh(), [], ["शीर्ष 5 खतरनाक जानवर", "Top 5"], cfg=CFG)
    assert not v.kept and v.reasons[0].startswith("no language tag; titles ")
    assert v.reasons[0].endswith("latin < 90%")


def test_keyword_overlap_below_minimum_is_a_reason() -> None:
    c = Candidate(
        "UCx",
        video_ids=["v"],
        hit_titles=["Cute cats"],
        item={"statistics": {"subscriberCount": "50000"}},
    )
    v = screen_channel(
        c, own_channel_id=OWN, rejected=set(), band=subs_band(CFG), terms={"snakes"}, cfg=CFG
    )
    assert not v.kept
    assert any(r.startswith("keyword overlap 0 < 2") for r in v.reasons)


def test_filter_keeps_exactly_the_two_good_channels(conn: sqlite3.Connection) -> None:
    result, _ = run(conn, FakeTransport(fixtures()))
    assert result.stopped is None
    assert result.queries == ["top 5 dangerous animals"]
    assert [v.channel_id for v in result.kept] == ["UCgood1", "UCgood2"]

    reasons = {v.channel_id: "; ".join(v.reasons) for v in result.dropped}
    assert set(reasons) == {OWN, "UCsmall1", "UCsmall2", "UClong", "UCrejected"}
    assert reasons[OWN] == "own channel"
    assert reasons["UCrejected"] == "rejected before"
    assert "subs 2,000 < 10,000 min" in reasons["UCsmall1"]
    assert "subs 500 < 10,000 min" in reasons["UCsmall2"]
    assert "shorts share 0% < 70% of 2 hit(s)" in reasons["UClong"]
    assert "top hit 400,000 views >= 100,000" in reasons["UClong"]  # viral, but long-form
    assert "topic: animals, snakes" in reasons["UClong"]

    good1 = result.verdicts["UCgood1"]
    assert (good1.hit_count, good1.keyword_overlap, good1.score) == (2, 2, 4)
    assert good1.matched_queries == ["top 5 dangerous animals"]


def test_survivors_are_written_with_discovery_json_videos_and_snapshots(
    conn: sqlite3.Connection,
) -> None:
    run(conn, FakeTransport(fixtures()))
    rows = conn.execute(
        "SELECT id, role, status, discovery_json FROM channels"
        " WHERE role = 'competitor' AND discovery_json IS NOT NULL ORDER BY id"
    ).fetchall()
    assert [r["id"] for r in rows] == ["UCgood1", "UCgood2"]
    assert all(r["role"] == "competitor" and r["status"] is None for r in rows)
    assert json.loads(rows[0]["discovery_json"]) == {
        "score": 4,
        "reasons": [
            "subs 50,000 >= 10,000",
            "keyword overlap 2: animals, dangerous",
            "topic: animals, snakes",
            "shorts share 100% >= 70% of 2 hit(s)",
            "top hit 900,000 views >= 100,000",
            "language en (2 of 2 tagged)",
        ],
        "hit_count": 2,
        "matched_queries": ["top 5 dangerous animals"],
    }
    good2 = json.loads(rows[1]["discovery_json"])["reasons"]
    assert good2[-1] == "no language tag; titles 100% latin >= 90%"
    good1 = repo.get_channel(conn, "UCgood1")
    assert good1["uploads_playlist_id"] == "UUgood1"
    assert repo.latest_channel_snapshot(conn, "UCgood1")["subs"] == 50000
    assert count(conn, "videos", "channel_id IN ('UCgood1', 'UCgood2')") == 4
    assert count(conn, "video_snapshots") == 4
    assert repo.get_video(conn, "good2vid002")["is_short"] == 1  # 3:00 is a Short
    for cid in ("UCsmall1", "UCsmall2", "UClong"):
        assert repo.get_channel(conn, cid) is None


def test_units_are_searches_times_100_plus_lookups(conn: sqlite3.Connection) -> None:
    transport = FakeTransport(fixtures())
    result, ledger = run(conn, transport)
    calls = [(r, m) for r, m, _ in transport.calls]
    searches = calls.count(("search", "list"))
    channel_calls = calls.count(("channels", "list"))
    video_calls = calls.count(("videos", "list"))
    assert (searches, channel_calls, video_calls) == (2, 2, 1)  # own + candidates batch
    assert ledger.run_used == searches * 100 + channel_calls + video_calls == 203
    assert conn.execute("SELECT SUM(units_used) FROM quota_ledger").fetchone()[0] == 203
    assert ledger.run_used < 2300
    assert result.searches == searches


def test_rejected_channel_never_reappears_and_rerun_is_idempotent(
    conn: sqlite3.Connection,
) -> None:
    run(conn, FakeTransport(fixtures()))
    first = {r["id"]: r["discovery_json"] for r in conn.execute("SELECT * FROM channels")}
    run(conn, FakeTransport(fixtures()))
    second = {r["id"]: r["discovery_json"] for r in conn.execute("SELECT * FROM channels")}
    assert first.keys() == second.keys()
    assert first == second
    assert count(conn, "channels") == 4  # own, rejected, good1, good2
    assert count(conn, "channel_snapshots", "channel_id = 'UCgood1'") == 2
    assert count(conn, "videos", "channel_id = 'UCgood1'") == 2
    assert count(conn, "video_snapshots", "video_id = 'good1vid001'") == 2

    rejected = repo.get_channel(conn, "UCrejected")
    assert (rejected["title"], rejected["status"]) == ("Seeded title", "rejected")
    assert rejected["discovery_json"] is None
    assert count(conn, "channel_snapshots", "channel_id = 'UCrejected'") == 0
    assert count(conn, "videos", "channel_id = 'UCrejected'") == 0


def test_approved_status_survives_a_rerun(conn: sqlite3.Connection) -> None:
    run(conn, FakeTransport(fixtures()))
    with conn:
        repo.set_channel_status(conn, "UCgood1", "approved")
    run(conn, FakeTransport(fixtures()))
    assert repo.get_channel(conn, "UCgood1")["status"] == "approved"


def test_worst_case_budget() -> None:
    assert worst_case_units(10) == 1 + 20 * 100 + 20 + 20 == 2041
    assert queries_within(None, 10) == 10
    assert queries_within(300, 10) == 1
    assert queries_within(2041, 10) == 10
    assert queries_within(200, 10) == 0


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(f"own_channel_id: {OWN}\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "config" / "scoring.yaml", config / "scoring.yaml")
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    c = connect(tmp_path / "data" / "ytscout.sqlite")
    seed(c)
    c.close()
    return tmp_path


def test_cli_discover_end_to_end(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport(fixtures())
    monkeypatch.setattr(cli, "make_transport", lambda settings, dry_run: transport)
    assert main(["discover", "--max-searches", "2", "--max-units", "300"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "2 searches, 7 hit channels, 2 kept, 5 dropped; units this run 203" in out
    c = sqlite3.connect(repo_root / "data" / "ytscout.sqlite")
    assert c.execute("SELECT kind, status FROM runs").fetchall() == [("discover", "ok")]
    assert (
        c.execute("SELECT COUNT(*) FROM channels WHERE discovery_json IS NOT NULL").fetchone()[0]
        == 2
    )


def test_cli_dry_run_plans_default_searches_and_writes_nothing(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = repo_root / "data" / "ytscout.sqlite"
    before = db.read_bytes()
    assert main(["discover", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "planned: 16 searches (1600 units) + 1 own channels.list = 1601 units" in out
    assert "top 5 dangerous animals" in out
    assert "worst case for this plan: 1633 units" in out
    assert db.read_bytes() == before


def test_cli_refuses_more_than_25_searches(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["discover", "--max-searches", "26", "--dry-run"]) == EXIT_ERROR
    assert "--max-searches must be 2 to 25" in capsys.readouterr().err


def test_cli_requires_max_units_or_dry_run(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["discover"]) == EXIT_ERROR
    assert "--max-units" in capsys.readouterr().err


def test_cli_max_units_too_small_for_one_query(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["discover", "--max-units", "150"]) == EXIT_ERROR
    assert "too small" in capsys.readouterr().err
