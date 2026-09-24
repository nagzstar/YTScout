"""``transcripts.fetch`` and ``collect --transcripts`` with the library faked at ``_fetcher``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from youtube_transcript_api import NoTranscriptFound, RequestBlocked, TranscriptsDisabled

from ytscout import transcripts
from ytscout.cli import EXIT_OK, main
from ytscout.collect.transcripts import collect_transcripts
from ytscout.store import connect, repo

OWN = "UCown"


@dataclass
class Snip:
    text: str
    start: float = 0.0
    duration: float = 1.0


@dataclass
class FakeTrack:
    language_code: str
    is_generated: bool
    texts: list[str] = field(default_factory=lambda: ["hello"])

    def fetch(self) -> list[Snip]:
        return [Snip(t, start=i * 2.0) for i, t in enumerate(self.texts)]


class FakeFetcher:
    """Per-video scripted answers: a list of tracks, or an exception to raise."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, video_id: str) -> list[FakeTrack]:
        self.calls.append(video_id)
        answer = self.answers[video_id]
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer()
        return list(answer)  # type: ignore[call-overload]


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(transcripts, "_sleep", recorded.append)
    return recorded


def use(monkeypatch: pytest.MonkeyPatch, answers: dict[str, object]) -> FakeFetcher:
    fake = FakeFetcher(answers)
    monkeypatch.setattr(transcripts, "_fetcher", fake)
    return fake


# --- choice and joining -----------------------------------------------------------------


def _codes(*tracks: FakeTrack) -> tuple[str, bool] | None:
    chosen = transcripts.choose(list(tracks))
    return None if chosen is None else (chosen.language_code, chosen.is_generated)


def test_preference_order() -> None:
    m_en, m_gb, g_en = FakeTrack("en", False), FakeTrack("en-GB", False), FakeTrack("en", True)
    m_de, g_fr = FakeTrack("de", False), FakeTrack("fr", True)
    assert _codes(g_fr, m_de, g_en, m_gb, m_en) == ("en", False)
    assert _codes(g_fr, m_de, g_en, m_gb) == ("en-GB", False)
    assert _codes(g_fr, m_de, g_en) == ("en", True)
    assert _codes(g_fr, m_de) == ("de", False)
    assert _codes(g_fr) == ("fr", True)
    assert _codes() is None


def test_fetch_joins_segments_and_records_language_and_source(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    use(
        monkeypatch,
        {
            "v1": [FakeTrack("en", True, ["  Number  five:\n", "the\tpangolin ", "", "wow"])],
            "v2": [FakeTrack("es", False, ["hola"]), FakeTrack("fr", True, ["salut"])],
        },
    )
    t = transcripts.fetch("v1")
    assert (t.status, t.language, t.source) == ("ok", "en", "auto")
    assert t.text == "Number five: the pangolin wow"
    t = transcripts.fetch("v2")
    assert (t.status, t.language, t.source, t.text) == ("ok", "es", "manual", "hola")
    assert sleeps == []


# --- failures -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [TranscriptsDisabled("v1"), NoTranscriptFound("v1", ["en"], None), transcripts.Unavailable()],
)
def test_unavailable_is_final_without_retry(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float], exc: Exception
) -> None:
    fake = use(monkeypatch, {"v1": exc})
    t = transcripts.fetch("v1")
    assert t.status == "unavailable"
    assert t.text is None and t.source is None and t.language == transcripts.NO_LANGUAGE
    assert fake.calls == ["v1"]
    assert sleeps == []


def test_no_tracks_is_unavailable(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    use(monkeypatch, {"v1": []})
    assert transcripts.fetch("v1").status == "unavailable"


@pytest.mark.parametrize("exc", [RequestBlocked("v1"), ConnectionError("reset"), OSError("x")])
def test_error_retries_three_times_with_backoff(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float], exc: Exception
) -> None:
    fake = use(monkeypatch, {"v1": exc})
    t = transcripts.fetch("v1")
    assert t.status == "error"
    assert fake.calls == ["v1"] * 4  # one try + three retries
    assert sleeps == [1.0, 4.0, 16.0]


def test_error_then_success_recovers(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    attempts = iter([ConnectionError("a"), ConnectionError("b"), [FakeTrack("en", False)]])

    def flaky() -> list[FakeTrack]:
        answer = next(attempts)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    use(monkeypatch, {"v1": flaky})
    t = transcripts.fetch("v1")
    assert t.status == "ok"
    assert sleeps == [1.0, 4.0]


# --- collector ----------------------------------------------------------------------------


def seeded() -> sqlite3.Connection:
    """Own: o1 (newest), o2. Approved: a1. Watch: w1. Rejected: r1. Un-reviewed: n1."""
    conn = connect(Path(":memory:"))
    with conn:
        repo.upsert_channel(conn, OWN, role="own")
        for cid, status in (("UCa", "approved"), ("UCw", "watch"), ("UCr", "rejected")):
            repo.upsert_channel(conn, cid, role="competitor")
            repo.set_channel_status(conn, cid, status)
        repo.upsert_channel(conn, "UCn", role="competitor")
        for vid, cid, day in (
            ("o1", OWN, "2026-09-20"),
            ("o2", OWN, "2026-09-01"),
            ("a1", "UCa", "2026-09-10"),
            ("w1", "UCw", "2026-08-01"),
            ("r1", "UCr", "2026-09-22"),
            ("n1", "UCn", "2026-09-23"),
        ):
            repo.upsert_video(conn, vid, channel_id=cid, published_at=f"{day}T00:00:00Z")
    return conn


def test_candidates_are_own_and_approved_or_watch_newest_first() -> None:
    conn = seeded()
    assert repo.transcript_candidates(conn, 100) == ["o1", "a1", "o2", "w1"]
    assert repo.transcript_candidates(conn, 2) == ["o1", "a1"]


def test_collect_writes_rows_counts_and_pauses_between_videos(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    conn = seeded()
    use(
        monkeypatch,
        {
            "o1": [FakeTrack("en", True, ["one"])],
            "a1": TranscriptsDisabled("a1"),
            "o2": [FakeTrack("en-GB", False, ["two"])],
            "w1": [FakeTrack("de", False, ["drei"])],
        },
    )
    counts = collect_transcripts(conn, pause_seconds=1.5)
    assert (counts.ok, counts.unavailable, counts.error) == (3, 1, 0)
    assert sleeps == [1.5, 1.5, 1.5]  # between four videos, not after the last
    row = repo.get_transcripts(conn, "o2")[0]
    assert (row["language"], row["source"], row["status"], row["text"]) == (
        "en-GB",
        "manual",
        "ok",
        "two",
    )
    assert repo.get_transcripts(conn, "a1")[0]["status"] == "unavailable"


def test_second_run_skips_ok_and_unavailable_and_retries_errors(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    conn = seeded()
    use(
        monkeypatch,
        {
            "o1": [FakeTrack("en", False)],
            "a1": TranscriptsDisabled("a1"),
            "o2": ConnectionError("down"),
            "w1": [FakeTrack("en", True)],
        },
    )
    first = collect_transcripts(conn, pause_seconds=0)
    assert (first.ok, first.unavailable, first.error) == (2, 1, 1)
    assert repo.get_transcripts(conn, "o2")[0]["status"] == "error"

    fake = use(monkeypatch, {"o2": [FakeTrack("en", True, ["back"])]})
    second = collect_transcripts(conn, pause_seconds=0)
    assert fake.calls == ["o2"]  # o1/w1 ok and a1 TranscriptsDisabled are not re-fetched
    assert (second.ok, second.unavailable, second.error) == (1, 0, 0)
    rows = repo.get_transcripts(conn, "o2")
    assert [(r["status"], r["language"]) for r in rows] == [("ok", "en")]  # error row cleared

    third = use(monkeypatch, {})
    assert collect_transcripts(conn, pause_seconds=0).ok == 0
    assert third.calls == []


def test_ok_row_is_never_overwritten() -> None:
    conn = seeded()
    with conn:
        repo.put_transcript(conn, "o1", language="en", text="kept", source="auto", status="ok")
        repo.put_transcript(conn, "o1", language="en", text="new", source="auto", status="ok")
    assert repo.get_transcripts(conn, "o1")[0]["text"] == "kept"


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text(
        f"own_channel_id: {OWN}\ntranscripts: {{pause_seconds: 0.25}}\n", encoding="utf-8"
    )
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed_db(root: Path) -> None:
    conn = connect(root / "data" / "ytscout.sqlite")
    with conn:
        repo.upsert_channel(conn, OWN, role="own")
        repo.upsert_video(conn, "o1", channel_id=OWN, published_at="2026-09-20T00:00:00Z")
        repo.upsert_video(conn, "o2", channel_id=OWN, published_at="2026-09-01T00:00:00Z")
    conn.close()


def test_cli_dry_run_with_no_db_says_none(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--transcripts", "--dry-run"]) == EXIT_OK
    assert "candidates: none" in capsys.readouterr().out
    assert not (repo_root / "data").exists()


def test_cli_dry_run_lists_ids_and_fetches_nothing(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_db(repo_root)
    fake = use(monkeypatch, {})
    assert main(["collect", "--transcripts", "--dry-run", "--limit", "1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "  o1" in out and "o2" not in out and "candidates: 1" in out
    assert fake.calls == []


def test_cli_real_run_prints_counts_and_uses_configured_pause(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_db(repo_root)
    use(monkeypatch, {"o1": [FakeTrack("en", True)], "o2": NoTranscriptFound("o2", ["en"], None)})
    assert main(["collect", "--transcripts"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "ok 1, unavailable 1, error 0" in captured.out
    assert "o2: unavailable (NoTranscriptFound)" in captured.err
    assert sleeps == [0.25]
