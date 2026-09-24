"""The quota ledger: one row per Pacific day in ``quota_ledger``.

YouTube resets the Data API quota at midnight America/Los_Angeles (08:00 in the UK), so the
ledger is keyed by the Pacific date, never the UK one. ``Ledger.charge`` runs **before**
each request: Google bills a request whether it succeeds, fails validation or returns 304,
so we do too.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ytscout.store import repo
from ytscout.store.db import utc_now

PACIFIC = ZoneInfo("America/Los_Angeles")

# Units per call, keyed by (resource, method). The only place a cost is written down.
UNIT_COSTS: dict[tuple[str, str], int] = {
    ("search", "list"): 100,
    ("channels", "list"): 1,
    ("playlistItems", "list"): 1,
    ("videos", "list"): 1,
}


class QuotaExhausted(Exception):
    """A charge would pass a cap, or Google itself said ``quotaExceeded``.

    ``kind`` is ``"daily"`` (the ledger's per-Pacific-day cap), ``"run"`` (this run's
    ``--max-units``) or ``"google"`` (Google refused; ``used`` and ``cap`` are ``None``
    because the ledger cannot see other users of the Cloud project). The CLI maps all
    three to exit code 3.
    """

    def __init__(self, kind: str, used: int | None = None, cap: int | None = None) -> None:
        self.kind = kind
        self.used = used
        self.cap = cap
        if kind == "google":
            message = "Google reported quotaExceeded for the Cloud project"
        else:
            message = f"{kind} quota cap reached: {used} of {cap} units used"
        super().__init__(message)


def today_pacific(now: datetime | None = None) -> date:
    """The America/Los_Angeles calendar date at ``now`` (an aware datetime; default: now)."""
    moment = utc_now() if now is None else now
    if moment.tzinfo is None:
        raise ValueError("naive datetime: pass an aware datetime")
    return moment.astimezone(PACIFIC).date()


class Ledger:
    """Counts units per Pacific day in SQLite and per run in memory.

    ``daily_cap`` is ``Settings.quota.daily_cap`` (9,000 by default, under Google's
    10,000). ``run_cap`` is the command's ``--max-units``. ``charge`` commits its own
    write so a crash never forgets units already spent; that also commits anything else
    pending on ``conn``.

    With ``dry_run=True`` the caps are checked against the stored day total plus this
    run's would-be units, but nothing is written: a ``--dry-run`` shows where a real run
    would stop without spending the ledger.

    ``clock`` returns an aware datetime; the default reads ``utc_now`` at each call.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        daily_cap: int,
        run_cap: int | None = None,
        *,
        dry_run: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if daily_cap < 0 or (run_cap is not None and run_cap < 0):
            raise ValueError("caps must be non-negative")
        self.conn = conn
        self.daily_cap = daily_cap
        self.run_cap = run_cap
        self.dry_run = dry_run
        self.run_used = 0
        self._clock = clock

    def today(self) -> str:
        """Today's ledger key, ``YYYY-MM-DD`` in Pacific time."""
        return today_pacific(self._clock() if self._clock else None).isoformat()

    def used_today(self) -> int:
        used = repo.quota_used(self.conn, self.today())
        return used + self.run_used if self.dry_run else used

    def charge(self, units: int) -> None:
        """Record ``units`` or raise ``QuotaExhausted`` without recording anything."""
        if units < 1:
            raise ValueError(f"a request costs at least 1 unit, got {units}")
        day = self.today()
        used = repo.quota_used(self.conn, day)
        if self.dry_run:
            used += self.run_used
        if used + units > self.daily_cap:
            raise QuotaExhausted("daily", used, self.daily_cap)
        if self.run_cap is not None and self.run_used + units > self.run_cap:
            raise QuotaExhausted("run", self.run_used, self.run_cap)
        if not self.dry_run:
            with self.conn:
                repo.add_quota_used(self.conn, day, units)
        self.run_used += units

    def remaining_today(self) -> int:
        return max(0, self.daily_cap - self.used_today())

    def remaining_run(self) -> int | None:
        return None if self.run_cap is None else max(0, self.run_cap - self.run_used)
