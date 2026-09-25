"""Checkpoints that let a quota-stopped run pick up where it stopped (032, DESIGN.md §13).

Every collector that loops over channels or niches records each finished unit of work in
``collector_state(kind, run_id, key, done_at)``, whether or not ``--resume`` was given.
``--resume`` skips the keys already done in the current *logical run*: the ISO week
(``2026-W39``) for ``own``, ``competitors`` and ``niches``; ``niche-<id>`` for ``validate``.
State older than ``STATE_TTL_DAYS`` is ignored, and a real run deletes it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ytscout.store import repo

KIND_OWN = "own"
KIND_COMPETITORS = "competitors"
KIND_NICHES = "niches"
KIND_VALIDATE = "validate"

STATE_TTL_DAYS = 10
# The Pacific-midnight quota reset, in UK time (DESIGN.md §8).
RESET_HINT = "after 08:00 UK"


def iso_week(now: datetime) -> str:
    """``YYYY-Www`` of ``now`` (ISO year and week, so 2027-01-01 can be ``2026-W53``)."""
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def validate_run_id(niche_id: int | str) -> str:
    return f"niche-{niche_id}"


@dataclass
class Checkpoint:
    """One collector's view of ``collector_state`` for one logical run.

    ``done`` holds the keys a ``--resume`` run skips; it is empty without ``--resume``.
    ``mark`` writes a row and commits it.
    """

    conn: sqlite3.Connection
    kind: str
    run_id: str
    now: datetime
    resume: bool = False
    done: set[str] = field(default_factory=set)
    skipped: list[str] = field(default_factory=list)

    def skip(self, key: str) -> bool:
        if key in self.done:
            self.skipped.append(key)
            return True
        return False

    def mark(self, key: str) -> None:
        with self.conn:
            repo.mark_collector_done(self.conn, self.kind, self.run_id, key, self.now)


def done_keys(conn: sqlite3.Connection | None, kind: str, run_id: str, now: datetime) -> set[str]:
    """Keys done in ``run_id`` within the last ``STATE_TTL_DAYS``; none without a DB."""
    if conn is None:
        return set()
    try:
        since = now - timedelta(days=STATE_TTL_DAYS)
        return repo.collector_done_keys(conn, kind, run_id, since)
    except sqlite3.Error:
        return set()  # a DB older than 0010 has no checkpoints to honour


def open_checkpoint(
    conn: sqlite3.Connection,
    kind: str,
    run_id: str,
    now: datetime,
    *,
    resume: bool,
    state: sqlite3.Connection | None = None,
    clear_stale: bool = True,
) -> Checkpoint:
    """The checkpoint a collector writes to ``conn``. With ``resume``, its ``done`` keys
    are read from ``state`` (default ``conn``; a dry run passes the real DB read-only).
    ``clear_stale`` deletes rows older than the TTL from ``conn`` first.
    """
    if clear_stale:
        with conn:
            repo.clear_collector_state(conn, now - timedelta(days=STATE_TTL_DAYS))
    done = done_keys(state if state is not None else conn, kind, run_id, now) if resume else set()
    return Checkpoint(conn, kind, run_id, now, resume=resume, done=done)


def resume_command(argv: Iterable[str]) -> str:
    """The exact command that continues this run: ``argv`` with ``--resume`` added."""
    args = list(argv)
    if "--resume" not in args:
        args.append("--resume")
    return r".venv\Scripts\python.exe -m ytscout " + " ".join(args)


def resume_hint(argv: Iterable[str]) -> str:
    return f"resume with: {resume_command(argv)} {RESET_HINT}"
