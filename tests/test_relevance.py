"""The niche relevance gate (049): which sampled channels count as in the niche."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from test_scoring import CFG, COVERAGE, NOW, STEPS, TABLE
from test_scout_score import CHANNELS, add_channel, build_example, run_score

from ytscout.dashboard import NicheContext, build
from ytscout.scoring import ScoringConfigError, load_scoring, relevance_config
from ytscout.scoring.config import SCORING_RELPATH
from ytscout.scoring.types import ChannelSample, VideoSample
from ytscout.scout import relevance
from ytscout.scout import score as scout_score
from ytscout.store import connect, read_copy, repo

REPO_ROOT = Path(__file__).resolve().parents[1]
RCFG = relevance_config(load_scoring(REPO_ROOT / SCORING_RELPATH))
TERMS = relevance.niche_terms(
    {
        "queries_json": '["top 5 most dangerous animals shorts", "deadliest animals countdown"]',
        "label": "Top 5 countdowns: dangerous animals",
        "topic": "dangerous-animals",
    }
)
ENTERTAINMENT = "24"
NEWS = "25"


def test_config_reads_the_shipped_thresholds_and_reuses_discovery() -> None:
    assert RCFG.news_category_ids == ("25",)
    assert RCFG.news_share_min == 0.5
    assert RCFG.topical_title_share_min == 0.1
    assert RCFG.titles_per_channel == 30  # niche_validation.videos_per_channel
    assert RCFG.language == "en" and RCFG.latin_share_min == 0.9  # discovery's


def test_config_rejects_a_missing_section_and_a_share_above_one() -> None:
    doc = load_scoring(REPO_ROOT / SCORING_RELPATH)
    doc["niche_validation"] = {k: v for k, v in doc["niche_validation"].items() if k != "relevance"}
    with pytest.raises(ScoringConfigError, match="relevance"):
        relevance_config(doc)
    doc = load_scoring(REPO_ROOT / SCORING_RELPATH)
    doc["niche_validation"]["relevance"]["news_share_min"] = 1.5
    with pytest.raises(ScoringConfigError, match="news_share_min"):
        relevance_config(doc)


def test_niche_terms_drop_format_and_stop_words() -> None:
    assert TERMS == {"dangerous", "animals", "deadliest", "countdowns"}


# ---------------------------------------------------------------- exclusion_reason (pure)


def test_english_topical_channel_passes() -> None:
    titles = ["Top 5 Deadliest Animals on Earth #shorts", "Why this spider is the worst"] * 5
    assert relevance.exclusion_reason(titles, [ENTERTAINMENT] * 10, TERMS, RCFG) is None


def test_news_outlet_fails_even_when_its_titles_match() -> None:
    titles = ["Dangerous animals escape from zoo", "Election results live"] * 5
    reason = relevance.exclusion_reason(titles, [NEWS] * 8 + [ENTERTAINMENT] * 2, TERMS, RCFG)
    assert reason == "news outlet: 8 of 10 videos in a news category"


def test_news_share_below_the_threshold_passes() -> None:
    titles = ["Dangerous animals explained"] * 10
    assert relevance.exclusion_reason(titles, [NEWS] * 4 + [None] * 6, TERMS, RCFG) is None


def test_channel_with_no_overlapping_keyword_fails() -> None:
    titles = ["Free Fire headshot montage", "My new gaming setup"] * 5
    reason = relevance.exclusion_reason(titles, [ENTERTAINMENT] * 10, TERMS, RCFG)
    assert reason == "off-topic: 0 of 10 titles share a niche word (0% < 10%)"


def test_mega_channel_with_one_on_topic_video_in_thirty_fails() -> None:
    titles = ["The deadliest thing in the universe"] + [
        f"How stars die, part {i}" for i in range(29)
    ]
    reason = relevance.exclusion_reason(titles, ["27"] * 30, TERMS, RCFG)
    assert reason == "off-topic: 1 of 30 titles share a niche word (3% < 10%)"
    # Exactly at the threshold counts: 3 of 30 is 10%.
    titles[1] = titles[2] = "Dangerous animals of the deep"
    assert relevance.exclusion_reason(titles, ["27"] * 30, TERMS, RCFG) is None


def test_non_latin_titles_fail() -> None:
    titles = ["सबसे खतरनाक जानवर dangerous animals"] * 10
    reason = relevance.exclusion_reason(titles, [ENTERTAINMENT] * 10, TERMS, RCFG)
    assert reason is not None and reason.startswith("not en: titles") and "< 90%" in reason


def test_latin_script_foreign_language_fails() -> None:
    titles = ["Les animaux les plus dangereux du monde", "La vérité sur le requin"] * 5
    reason = relevance.exclusion_reason(titles, [ENTERTAINMENT] * 10, TERMS, RCFG)
    assert reason == "not en: 10 of 10 titles use non-English words, 0 English"


def test_hashtag_titles_without_function_words_are_not_called_foreign() -> None:
    titles = ["Dangerous animals #shorts #wildlife"] * 10
    assert relevance.exclusion_reason(titles, [ENTERTAINMENT] * 10, TERMS, RCFG) is None


def test_foreign_check_only_runs_for_english() -> None:
    titles = ["Les animaux dangerous"] * 10
    spanish = replace(RCFG, language="fr")
    assert relevance.exclusion_reason(titles, [None] * 10, TERMS, spanish) is None


def test_channel_without_videos_is_kept() -> None:
    assert relevance.exclusion_reason([], [], TERMS, RCFG) is None


# ---------------------------------------------------------------- the DB side


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def add_news_outlet(conn: sqlite3.Connection, niche_id: int) -> None:
    """A big channel whose every video is News & Politics, one of them in the window."""
    news = ChannelSample(
        channel_id="NEWS",
        subs=20_000_000,
        created_at=NOW.replace(year=2006),
        videos=[VideoSample(views=5_000_000, published_at=NOW, duration_s=40)],
    )
    with conn:
        add_channel(conn, news, niche_id)
        conn.execute("UPDATE videos SET category_id = ? WHERE channel_id = 'UCNEWS'", (NEWS,))


def run_score_with_gate(conn: sqlite3.Connection) -> scout_score.NicheScoreResult:
    return scout_score.score_niches(
        conn,
        cfg=CFG,
        rpm=TABLE,
        steps=STEPS,
        coverage=COVERAGE,
        usd_gbp=0.78,
        now=NOW,
        relevance=RCFG,
    )


def test_score_excludes_a_news_outlet_and_restores_the_worked_example(
    conn: sqlite3.Connection,
) -> None:
    # The test_scout_score helpers title every video "<key> animals video <i> (...)": the
    # example's six channels (and the news outlet) share "animals" with the niche label.
    niche_id = build_example(conn)
    add_news_outlet(conn, niche_id)

    polluted = run_score(conn).scored[0]
    assert polluted.sampled == 7 and polluted.excluded == 0

    gated = run_score_with_gate(conn).scored[0]
    assert gated.sampled == 7 and gated.excluded == 1
    assert round(gated.values["opportunity"], 3) == 0.387  # 023's worked example again
    assert round(polluted.values["opportunity"], 3) != 0.387
    (row,) = repo.excluded_niche_channels(conn, niche_id)
    assert row["channel_id"] == "UCNEWS"
    assert row["excluded_reason"].startswith("news outlet")
    ids = {c.channel_id for c in scout_score.build_sample(conn, niche_id, CFG, now=NOW).channels}
    assert ids == {f"UC{k}" for k in CHANNELS}
    table = scout_score.format_table(run_score_with_gate(conn))
    assert " 6/7 " in table and "kept" in table.splitlines()[0]


def test_without_the_gate_stored_verdicts_stand(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn)
    add_news_outlet(conn, niche_id)
    with conn:
        repo.set_niche_channel_exclusion(conn, niche_id, "UCNEWS", "news outlet: set by hand")
    scored = run_score(conn).scored[0]
    assert scored.excluded == 1
    assert repo.excluded_niche_channels(conn, niche_id)[0]["excluded_reason"] == (
        "news outlet: set by hand"
    )


def test_gate_screens_shelved_niches_it_does_not_score(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn, status="shelve")
    add_news_outlet(conn, niche_id)
    result = run_score_with_gate(conn)
    assert result.scored == []
    assert [r["channel_id"] for r in repo.excluded_niche_channels(conn, niche_id)] == ["UCNEWS"]


def test_a_rescore_brings_a_channel_back_when_it_passes(conn: sqlite3.Connection) -> None:
    niche_id = build_example(conn)
    add_news_outlet(conn, niche_id)
    with conn:
        assert relevance.refresh(conn, niche_id, RCFG) == (6, 1)
        assert relevance.refresh(conn, niche_id, replace(RCFG, news_category_ids=())) == (7, 0)
    assert repo.excluded_niche_channels(conn, niche_id) == []
    with conn:
        conn.execute(
            "UPDATE niches SET queries_json = '[\"zebra\"]', label = 'Zebras', topic = 'zebras'"
            " WHERE id = ?",
            (niche_id,),
        )
        assert relevance.refresh(conn, niche_id, RCFG) == (0, 7)


def test_dashboard_niche_detail_lists_the_excluded_channels(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    niche_id = build_example(conn)
    add_news_outlet(conn, niche_id)
    with conn:
        repo.upsert_channel(conn, "UCNEWS", role="niche_sample", title="Big News Network")
    run_score_with_gate(conn)
    copy = read_copy(Path(conn.execute("PRAGMA database_list").fetchone()["file"]))
    try:
        build(copy, tmp_path / "index.html", now=NOW, context=NicheContext(CFG, COVERAGE))
    finally:
        copy.close()
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    sample = html.split(f'id="niche-sample-{niche_id}"', 1)[1].split("</tr>", 1)[0]
    assert "Excluded from the sample (1)" in sample
    assert "Big News Network" in sample and "news outlet: 1 of 1 videos" in sample
