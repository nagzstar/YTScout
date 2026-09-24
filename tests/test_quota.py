"""The quota ledger: caps, Pacific days, dry runs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ytscout.store import repo
from ytscout.store.db import connect
from ytscout.youtube import quota
from ytscout.youtube.quota import Ledger, QuotaExhausted, today_pacific


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


# 2026-09-24 12:00 UTC is 05:00 PDT on the 24th.
NOON_UTC = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def test_today_pacific_is_los_angeles_date() -> None:
    # 06:59 UTC on the 25th is 23:59 PDT on the 24th; 07:00 UTC is midnight PDT.
    assert today_pacific(datetime(2026, 9, 25, 6, 59, tzinfo=UTC)) == date(2026, 9, 24)
    assert today_pacific(datetime(2026, 9, 25, 7, 0, tzinfo=UTC)) == date(2026, 9, 25)
    # Winter: PST is UTC-8, so midnight Pacific is 08:00 UTC = 08:00 in the UK.
    assert today_pacific(datetime(2026, 1, 10, 7, 59, tzinfo=UTC)) == date(2026, 1, 9)
    assert today_pacific(datetime(2026, 1, 10, 8, 0, tzinfo=UTC)) == date(2026, 1, 10)
    with pytest.raises(ValueError):
        today_pacific(datetime(2026, 1, 10, 8, 0))


def test_charge_adds_to_the_pacific_day_row(conn: sqlite3.Connection) -> None:
    ledger = Ledger(conn, daily_cap=9000, clock=Clock(NOON_UTC))
    ledger.charge(100)
    ledger.charge(1)
    assert repo.quota_used(conn, "2026-09-24") == 101
    assert ledger.remaining_today() == 8899
    assert ledger.run_used == 101
    assert ledger.remaining_run() is None


def test_charge_commits(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite"
    c = connect(path)
    Ledger(c, daily_cap=9000, clock=Clock(NOON_UTC)).charge(7)
    other = connect(path)
    assert repo.quota_used(other, "2026-09-24") == 7
    c.close()
    other.close()


def test_the_9001st_unit_raises_daily_and_the_row_stays_at_9000(
    conn: sqlite3.Connection,
) -> None:
    ledger = Ledger(conn, daily_cap=9000, clock=Clock(NOON_UTC))
    for _ in range(90):
        ledger.charge(100)
    assert repo.quota_used(conn, "2026-09-24") == 9000
    with pytest.raises(QuotaExhausted) as info:
        ledger.charge(1)
    assert (info.value.kind, info.value.used, info.value.cap) == ("daily", 9000, 9000)
    assert repo.quota_used(conn, "2026-09-24") == 9000
    assert ledger.remaining_today() == 0


def test_daily_cap_counts_units_from_earlier_runs(conn: sqlite3.Connection) -> None:
    Ledger(conn, daily_cap=9000, clock=Clock(NOON_UTC)).charge(8950)
    second_run = Ledger(conn, daily_cap=9000, clock=Clock(NOON_UTC))
    with pytest.raises(QuotaExhausted) as info:
        second_run.charge(100)
    assert info.value.kind == "daily"
    second_run.charge(50)
    assert repo.quota_used(conn, "2026-09-24") == 9000


def test_run_cap_300_stops_the_run_while_the_day_is_well_under(
    conn: sqlite3.Connection,
) -> None:
    ledger = Ledger(conn, daily_cap=9000, run_cap=300, clock=Clock(NOON_UTC))
    for _ in range(300):
        ledger.charge(1)
    with pytest.raises(QuotaExhausted) as info:
        ledger.charge(1)
    assert (info.value.kind, info.value.used, info.value.cap) == ("run", 300, 300)
    assert repo.quota_used(conn, "2026-09-24") == 300
    assert ledger.remaining_run() == 0
    assert ledger.remaining_today() == 8700


def test_rollover_happens_at_pacific_midnight(conn: sqlite3.Connection) -> None:
    clock = Clock(datetime(2026, 9, 25, 6, 59, tzinfo=UTC))  # 23:59 PDT on the 24th
    ledger = Ledger(conn, daily_cap=9000, clock=clock)
    ledger.charge(9000)
    with pytest.raises(QuotaExhausted):
        ledger.charge(1)
    clock.now = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)  # 08:00 UK, midnight Pacific
    ledger.charge(1)
    assert repo.quota_used(conn, "2026-09-24") == 9000
    assert repo.quota_used(conn, "2026-09-25") == 1


def test_default_clock_can_be_monkeypatched(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quota, "utc_now", lambda: datetime(2026, 12, 31, 23, 0, tzinfo=UTC))
    assert today_pacific() == date(2026, 12, 31)
    # 23:00 UTC on 31 Dec is 15:00 PST: the ledger row is still the 31st.
    ledger = Ledger(conn, daily_cap=10)
    assert ledger.today() == "2026-12-31"
    ledger.charge(10)
    assert repo.quota_used(conn, "2026-12-31") == 10


def test_dry_run_checks_caps_but_writes_nothing(conn: sqlite3.Connection) -> None:
    Ledger(conn, daily_cap=9000, clock=Clock(NOON_UTC)).charge(8900)
    dry = Ledger(conn, daily_cap=9000, dry_run=True, clock=Clock(NOON_UTC))
    dry.charge(100)
    with pytest.raises(QuotaExhausted) as info:
        dry.charge(1)
    assert info.value.kind == "daily"
    assert repo.quota_used(conn, "2026-09-24") == 8900


def test_charge_refuses_zero_units(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        Ledger(conn, daily_cap=9000).charge(0)


def test_google_kind_message_has_no_numbers() -> None:
    exc = QuotaExhausted("google")
    assert exc.used is None and exc.cap is None
    assert "quotaExceeded" in str(exc)
