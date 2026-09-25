"""Quota day-splitting (032): checkpoints per unit of work, ``--resume`` skips them.

A run capped by ``--max-units`` stands in for the ledger refusing mid-loop. The transport
answers any id, so a resumed run's smaller ``channels.list`` batch needs no new fixture.
Costs, by hand: competitors = 1 (channels batch) + 2 per channel (one page, one videos
batch); niches = 3 per one-channel niche (channels, page, videos).
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_scout_validate import FixtureTransport, add_niche

from ytscout import cli
from ytscout.cli import EXIT_OK, EXIT_QUOTA_EXHAUSTED, main
from ytscout.collect import resume as r
from ytscout.collect.competitors import collect_competitors
from ytscout.collect.niches import collect_niches
from ytscout.scout import validate as v
from ytscout.store import connect, repo, to_utc_iso
from ytscout.youtube import DataApi, DryRunTransport, Ledger

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)  # Tuesday of 2026-W39
WEEK = "2026-W39"
OWN = "UCown"
COMPETITORS = ["UCa", "UCb", "UCc"]


class AnyTransport:
    """Answers every id: one upload per channel, one Short per video id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, resource: str, method: str, *, etag: str | None = None, **params: Any) -> dict:
        self.calls.append((resource, params))
        if resource == "channels":
            return {
                "items": [
                    {
                        "id": cid,
                        "snippet": {"title": cid},
                        "statistics": {"subscriberCount": "1", "viewCount": "1"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid}},
                    }
                    for cid in params["id"].split(",")
                ]
            }
        if resource == "playlistItems":
            cid = params["playlistId"][2:]
            published = to_utc_iso(NOW - timedelta(days=1))
            return {
                "items": [{"contentDetails": {"videoId": f"v{cid}", "videoPublishedAt": published}}]
            }
        return {
            "items": [
                {
                    "id": vid,
                    "snippet": {"channelId": vid[1:], "title": vid, "publishedAt": "2026-09-21"},
                    "contentDetails": {"duration": "PT30S"},
                    "statistics": {"viewCount": "10"},
                }
                for vid in params["id"].split(",")
            ]
        }

    def channel_batches(self) -> list[str]:
        return [p["id"] for res, p in self.calls if res == "channels"]


def seed(conn: sqlite3.Connection) -> None:
    with conn:
        repo.upsert_channel(conn, OWN, role="own", title="Countdown")
        for cid in COMPETITORS:
            repo.upsert_channel(conn, cid, role="competitor", title=cid)
            repo.set_channel_status(conn, cid, "approved")


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "db.sqlite")
    seed(c)
    yield c
    c.close()


def state(conn: sqlite3.Connection, kind: str) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT run_id, key FROM collector_state WHERE kind = ?", (kind,))
    return {(row[0], row[1]) for row in rows}


def competitors(
    conn: sqlite3.Connection, *, run_cap: int | None, resume: bool, now: datetime = NOW
) -> tuple[Any, AnyTransport]:
    transport = AnyTransport()
    checkpoint = r.open_checkpoint(conn, r.KIND_COMPETITORS, r.iso_week(now), now, resume=resume)
    result = collect_competitors(
        DataApi(Ledger(conn, 9000, run_cap=run_cap, clock=lambda: now), transport),
        conn,
        [dict(row) for row in repo.tracked_channels(conn)],
        own_channel_id=OWN,
        shorts_max_seconds=180,
        now=now,
        checkpoint=checkpoint,
    )
    return result, transport


# --- helpers ------------------------------------------------------------------------------


def test_iso_week_uses_the_iso_year() -> None:
    assert r.iso_week(NOW) == WEEK
    assert r.iso_week(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-W53"


def test_resume_command_adds_the_flag_once() -> None:
    argv = ["collect", "--competitors", "--max-units", "5"]
    expected = r".venv\Scripts\python.exe -m ytscout collect --competitors --max-units 5 --resume"
    assert r.resume_command(argv) == expected
    assert r.resume_command([*argv, "--resume"]) == expected
    assert r.resume_hint(argv) == f"resume with: {expected} after 08:00 UK"


# --- competitors ----------------------------------------------------------------------------


def test_quota_stop_checkpoints_finished_channels_only(conn: sqlite3.Connection) -> None:
    # 1 (batch) + 2 (own) + 2 (UCa) = 5; UCb's playlist page is refused.
    result, _ = competitors(conn, run_cap=5, resume=False)
    assert result.stopped is not None and result.stopped.kind == "run"
    assert state(conn, "competitors") == {(WEEK, OWN), (WEEK, "UCa")}


def test_resume_processes_only_the_remainder_and_finishes(conn: sqlite3.Connection) -> None:
    competitors(conn, run_cap=5, resume=False)
    result, transport = competitors(conn, run_cap=None, resume=True)
    assert result.stopped is None
    assert transport.channel_batches() == ["UCb,UCc"]
    assert [rep.channel_id for rep in result.reports] == ["UCb", "UCc"]
    assert state(conn, "competitors") == {(WEEK, c) for c in [OWN, *COMPETITORS]}


def test_without_resume_it_starts_over(conn: sqlite3.Connection) -> None:
    competitors(conn, run_cap=5, resume=False)
    result, transport = competitors(conn, run_cap=None, resume=False)
    assert result.stopped is None
    assert transport.channel_batches() == [",".join([OWN, *COMPETITORS])]


def test_state_from_another_iso_week_is_ignored(conn: sqlite3.Connection) -> None:
    last_week = NOW - timedelta(days=7)
    competitors(conn, run_cap=None, resume=False, now=last_week)
    assert {run_id for run_id, _ in state(conn, "competitors")} == {"2026-W38"}
    result, transport = competitors(conn, run_cap=None, resume=True)
    assert transport.channel_batches() == [",".join([OWN, *COMPETITORS])]
    assert len(result.reports) == 4


def test_state_older_than_ten_days_is_ignored_and_cleared(conn: sqlite3.Connection) -> None:
    with conn:
        repo.mark_collector_done(conn, "validate", "niche-7", "7", NOW - timedelta(days=11))
        repo.mark_collector_done(conn, "validate", "niche-8", "8", NOW - timedelta(days=9))
    assert r.done_keys(conn, "validate", "niche-7", NOW) == set()
    r.open_checkpoint(conn, r.KIND_COMPETITORS, WEEK, NOW, resume=True)
    assert state(conn, "validate") == {("niche-8", "8")}


# --- niches ---------------------------------------------------------------------------------


def niche(nid: int, *channels: str) -> dict:
    return {
        "id": nid,
        "label": f"niche {nid}",
        "channels": [{"id": c, "uploads_playlist_id": "UU" + c} for c in channels],
    }


def test_niches_resume_after_a_stop(conn: sqlite3.Connection) -> None:
    niches = [niche(1, "UCn1"), niche(2, "UCn2"), niche(3, "UCn1", "UCn3")]

    def refresh(run_cap: int | None, resume: bool) -> tuple[Any, AnyTransport]:
        transport = AnyTransport()
        checkpoint = r.open_checkpoint(conn, r.KIND_NICHES, WEEK, NOW, resume=resume)
        result = collect_niches(
            DataApi(Ledger(conn, 9000, run_cap=run_cap, clock=lambda: NOW), transport),
            conn,
            niches,
            shorts_max_seconds=180,
            now=NOW,
            checkpoint=checkpoint,
        )
        return result, transport

    first, _ = refresh(4, False)  # niche 1 costs 3; niche 2 stops on its playlist page
    assert first.stopped is not None and first.finished == [1]
    assert state(conn, "niches") == {(WEEK, "1")}

    second, transport = refresh(None, True)
    assert second.stopped is None and second.finished == [2, 3]
    # UCn1 was refreshed under niche 1 before the stop: not again under niche 3.
    assert transport.channel_batches() == ["UCn2", "UCn3"]
    assert second.reports[1].shared == 1
    assert state(conn, "niches") == {(WEEK, "1"), (WEEK, "2"), (WEEK, "3")}


# --- validate --------------------------------------------------------------------------------


def test_validate_checkpoints_each_niche_and_resume_skips_it(tmp_path: Path) -> None:
    conn = connect(tmp_path / "v.sqlite")
    nid = add_niche(conn)

    def run(resume: bool) -> tuple[v.ValidateResult, FixtureTransport]:
        transport = FixtureTransport()
        api = DataApi(Ledger(conn, 9000, run_cap=1000, clock=lambda: NOW), transport)
        result = v.validate_niches(
            api,
            conn,
            [repo.get_niche(conn, nid)],
            cfg=v.ValidationConfig(10_000, 365, 365, 80, 4, 30, "en"),
            shorts_max_seconds=180,
            now=NOW,
            resume=resume,
        )
        return result, transport

    first, _ = run(False)
    assert len(first.validated) == 1
    assert state(conn, "validate") == {(f"niche-{nid}", str(nid))}

    skipped, transport = run(True)
    assert skipped.skipped == [nid] and skipped.reports == [] and transport.calls == []

    again, transport = run(False)
    assert len(again.validated) == 1 and transport.calls
    conn.close()


# --- CLI ------------------------------------------------------------------------------------


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
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        cli, "make_transport", lambda s, dry_run: DryRunTransport() if dry_run else AnyTransport()
    )
    c = connect(tmp_path / "data" / "ytscout.sqlite")
    seed(c)
    c.close()
    return tmp_path


def test_cli_quota_stop_prints_the_resume_command_then_resumes(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["collect", "--competitors", "--max-units", "5"]
    assert main(argv) == EXIT_QUOTA_EXHAUSTED
    err = capsys.readouterr().err
    assert (
        r"resume with: .venv\Scripts\python.exe -m ytscout collect --competitors"
        r" --max-units 5 --resume after 08:00 UK"
    ) in err

    assert main(["collect", "--competitors", "--dry-run", "--resume"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"resume: would skip 2 channel(s) already done in {WEEK}: {OWN}, UCa" in out
    assert "planned: 5 calls, 5 units for 2 tracked channel(s)" in out

    assert main([*argv, "--resume"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"resume: skipping 2 channel(s) already done in {WEEK}: {OWN}, UCa" in out
    assert "collect --competitors: channels 2, videos 2" in out


def test_cli_own_resume_skips_a_finished_week(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--own", "--max-units", "50"]) == EXIT_OK
    capsys.readouterr()
    assert main(["collect", "--own", "--max-units", "50", "--resume"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"resume: skipping 1 channel(s) already done in {WEEK}: {OWN}" in out
    assert "units this run 0" in out
