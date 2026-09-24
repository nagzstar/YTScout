"""Command-line entry point: ``python -m ytscout <command>``.

Exit codes (DESIGN.md §9.2): 0 ok · 1 error · 2 not implemented yet · 3 quota exhausted ·
4 no OAuth token · 5 ``claude`` unavailable. Stubs exit 2 so ``scripts/run_weekly.ps1``
can skip steps that no issue has delivered yet.
"""

from __future__ import annotations

import argparse
import math
import platform
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ytscout import __version__
from ytscout.audit import COVERAGE_RELPATH, STEPS_RELPATH, AuditError, format_table, run_audit
from ytscout.collect import ChannelNotFound, Counts, collect_own
from ytscout.scoring import SCORING_RELPATH, ScoringConfigError, load_scoring, shorts_max_seconds
from ytscout.settings import (
    DEFAULT_DATA_DIR,
    DEFAULT_QUOTA_DAILY_CAP,
    Settings,
    SettingsError,
    SettingsMissing,
    find_repo_root,
    load,
)
from ytscout.store import DB_FILENAME, connect, default_db_path, repo
from ytscout.youtube import (
    DataApi,
    DryRunTransport,
    GoogleTransport,
    Ledger,
    Transport,
)
from ytscout.youtube.client import PAGE_SIZE

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_QUOTA_EXHAUSTED = 3
EXIT_NO_OAUTH_TOKEN = 4
EXIT_CLAUDE_UNAVAILABLE = 5

# command -> (help text, issue that delivers it). Order is the order shown in --help.
STUBS: dict[str, tuple[str, str]] = {
    "auth": ("OAuth consent for the Analytics API", "011"),
    "discover": ("find candidate competitors", "006"),
    "packet": ("write one analysis packet for Claude", "016"),
    "analyse": ("run Claude analyses over packets", "017"),
    "score": ("compute scores from the DB", "024"),
    "scout": ("the niche pipeline: propose / validate / tag / snowball / sensitivity", "021"),
    "dashboard": ("build dashboard/index.html", "007"),
    "serve": ("localhost review server for approve/reject", "008"),
}

COMMANDS: tuple[str, ...] = ("doctor", "collect", *STUBS, "audit")

DEFAULT_OWN_VIDEOS = 200
# Shown in a --dry-run plan when config/settings.yaml does not exist yet.
DRY_RUN_CHANNEL_ID = "<own_channel_id>"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytscout",
        description="YT Scout: competitor analysis and niche scouting, locally.",
    )
    parser.add_argument("--version", action="version", version=f"ytscout {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    doctor = sub.add_parser("doctor", help="check settings and secrets are present (no values)")
    doctor.set_defaults(func=cmd_doctor)

    collect = sub.add_parser(
        "collect", help="pull own/competitor/analytics/transcript/niche data into SQLite"
    )
    collect.add_argument(
        "--own", action="store_true", help="the own channel: metadata, snapshot, newest uploads"
    )
    collect.add_argument(
        "--videos",
        type=_positive_int,
        default=DEFAULT_OWN_VIDEOS,
        help=f"newest uploads to collect (default {DEFAULT_OWN_VIDEOS})",
    )
    _add_quota_flags(collect)
    collect.set_defaults(func=cmd_collect)

    for name, (help_text, issue) in STUBS.items():
        stub = sub.add_parser(name, help=f"{help_text} (issue {issue})")
        stub.set_defaults(func=_make_stub(name, issue), stub=True)

    audit = sub.add_parser(
        "audit",
        help="validate and print the pipeline coverage table (no API, no network)",
    )
    audit.add_argument(
        "--steps",
        type=Path,
        default=None,
        help=f"production steps YAML (default: <repo root>/{STEPS_RELPATH.as_posix()})",
    )
    audit.add_argument(
        "--coverage",
        type=Path,
        default=None,
        help=f"pipeline coverage YAML (default: <repo root>/{COVERAGE_RELPATH.as_posix()})",
    )
    audit.set_defaults(func=cmd_audit)

    return parser


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {number}")
    return number


def _add_quota_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-units",
        type=_non_negative_int,
        default=None,
        help="stop this run after N Data API units (required unless --dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned API calls and their unit costs; write nothing",
    )


def _require_quota_flag(args: argparse.Namespace, command: str) -> bool:
    """The CLI's own rule: no API-touching run without ``--max-units N`` or ``--dry-run``."""
    if args.dry_run or args.max_units is not None:
        return True
    print(
        f"ytscout {command}: refusing to run without --max-units N (a cap on this run's "
        "YouTube Data API units) or --dry-run (plan only). The weekly job passes "
        "--max-units 8000.",
        file=sys.stderr,
    )
    return False


@dataclass
class RunRecord:
    """The ``runs`` row a command is writing; set ``status`` before the block ends."""

    id: int
    status: str = "ok"


@contextmanager
def recorded_run(conn: sqlite3.Connection, kind: str) -> Iterator[RunRecord]:
    """Record a ``runs`` row around a DB-touching command: start, finish, kind, status.

    An exception escaping the block marks the row ``error`` and propagates.
    """
    with conn:
        record = RunRecord(repo.start_run(conn, kind))
    try:
        yield record
    except BaseException:
        record.status = "error"
        raise
    finally:
        with conn:
            repo.finish_run(conn, record.id, record.status)


def make_transport(settings: Settings | None, dry_run: bool) -> Transport:
    """The Data API transport for a command. Tests replace this with a ``FakeTransport``."""
    if dry_run:
        return DryRunTransport()
    assert settings is not None
    return GoogleTransport(settings.api_key)


def _dry_run_connection(db_path: Path) -> sqlite3.Connection:
    """An in-memory DB seeded with the ledger totals from ``db_path``, if it exists.

    A dry run must not create, migrate or write the real database, so the real file is
    only ever opened read-only.
    """
    conn = connect(Path(":memory:"))
    if not db_path.is_file():
        return conn
    try:
        real = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = real.execute("SELECT day_pacific, units_used FROM quota_ledger").fetchall()
        finally:
            real.close()
    except sqlite3.Error as exc:
        print(f"note: could not read today's quota from {db_path}: {exc}", file=sys.stderr)
        return conn
    with conn:
        for day, units in rows:
            repo.add_quota_used(conn, day, units)
    return conn


def _format_params(params: dict) -> str:
    shown = {k: v for k, v in params.items() if k in ("id", "playlistId", "pageToken")}
    return " ".join(f"{k}={v}" for k, v in shown.items())


def _print_plan(transport: DryRunTransport, videos: int) -> None:
    print("dry run: planned YouTube Data API calls (nothing is sent, nothing is written)")
    for resource, method, params, units in transport.calls:
        print(f"  {resource}.{method} {_format_params(params)} - {units} unit(s)")
    print(f"planned: {len(transport.calls)} calls, {transport.units} units")
    pages = math.ceil(videos / PAGE_SIZE)
    print(
        f"a longer playlist pages on: at most {pages} playlist page(s) and {pages} videos "
        f"batch(es) for --videos {videos}, so at most {1 + 2 * pages} units"
    )


def _print_summary(counts: Counts, ledger: Ledger) -> None:
    print(
        f"collect --own: channels {counts.channels}, videos {counts.videos}, "
        f"snapshots {counts.snapshots} ({counts.channel_snapshots} channel, "
        f"{counts.video_snapshots} video); units this run {ledger.run_used}, "
        f"units today {ledger.used_today()}"
    )


def cmd_collect(args: argparse.Namespace, _extras: list[str]) -> int:
    """``collect --own``: channels → uploads playlist → videos → snapshots.

    Exit 3 when a quota cap stops the run; everything collected before it is committed.
    """
    if not args.own:
        print("ytscout collect: choose a source: --own", file=sys.stderr)
        return EXIT_ERROR
    if not _require_quota_flag(args, "collect"):
        return EXIT_ERROR

    root = find_repo_root()
    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"collect: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; planning with a placeholder channel id")
        settings = None
    except SettingsError as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        shorts_max = shorts_max_seconds(load_scoring(root / SCORING_RELPATH))
    except ScoringConfigError as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return EXIT_ERROR

    channel_id = settings.own_channel_id if settings else DRY_RUN_CHANNEL_ID
    daily_cap = settings.quota.daily_cap if settings else DEFAULT_QUOTA_DAILY_CAP
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME
    try:
        transport = make_transport(settings, args.dry_run)
    except ValueError as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.dry_run:
        conn = _dry_run_connection(db_path)
        try:
            ledger = Ledger(conn, daily_cap, run_cap=args.max_units, dry_run=True)
            counts = collect_own(
                DataApi(ledger, transport),
                conn,
                channel_id,
                videos=args.videos,
                shorts_max_seconds=shorts_max,
            )
        finally:
            conn.close()
        if isinstance(transport, DryRunTransport):
            _print_plan(transport, args.videos)
        if counts.stopped is not None:
            print(f"a real run would stop here: {counts.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK

    conn = connect(db_path)
    try:
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        with recorded_run(conn, "collect_own") as run:
            try:
                counts = collect_own(
                    DataApi(ledger, transport),
                    conn,
                    channel_id,
                    videos=args.videos,
                    shorts_max_seconds=shorts_max,
                )
            except ChannelNotFound as exc:
                run.status = "error"
                print(f"collect: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if counts.stopped is not None:
                run.status = "quota_exhausted"
        _print_summary(counts, ledger)
        if counts.stopped is not None:
            print(
                f"stopped early; everything above is committed: {counts.stopped}", file=sys.stderr
            )
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def _make_stub(name: str, issue: str):
    def run(_args: argparse.Namespace, _extras: list[str]) -> int:
        print(f"ytscout {name}: not implemented yet (issue {issue})", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    return run


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def cmd_doctor(_args: argparse.Namespace, _extras: list[str]) -> int:
    """Report what is configured, naming files but never printing their contents."""
    print(f"python: {platform.python_version()} ({sys.executable})")
    try:
        settings: Settings = load()
    except SettingsError as exc:
        print(f"settings: MISSING - {exc}")
        return EXIT_ERROR

    rel = _relative_to_root(settings)
    print(f"settings: ok ({rel(settings.settings_path)})")
    print(f"repo root: {settings.repo_root}")
    print(f"own_channel_id: {_yes_no(bool(settings.own_channel_id))}")
    print(f"YT_API_KEY: {_yes_no(settings.has_api_key)}")
    print(
        f"client secret: {rel(settings.client_secret_path)}: "
        f"{_yes_no(settings.client_secret_path.is_file())}"
    )
    print(f"oauth token: {rel(settings.token_path)}: {_yes_no(settings.token_path.is_file())}")
    return EXIT_OK


def cmd_audit(args: argparse.Namespace, _extras: list[str]) -> int:
    """Cross-check production_steps.yaml against pipeline_coverage.yaml and print the table.

    Exit 1 when either file is invalid or a step is missing from one side.
    """
    root = find_repo_root()
    steps_path = args.steps if args.steps is not None else root / STEPS_RELPATH
    coverage_path = args.coverage if args.coverage is not None else root / COVERAGE_RELPATH
    try:
        report = run_audit(steps_path, coverage_path)
    except AuditError as exc:
        print(f"audit: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(format_table(report))
    return EXIT_OK


def _relative_to_root(settings: Settings):
    def rel(path) -> str:
        try:
            return str(path.relative_to(settings.repo_root))
        except ValueError:
            return str(path)

    return rel


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    # Stubs accept any flags a later issue will define (`collect --own --max-units 500`)
    # and still exit 2, so run_weekly.ps1 can skip them. Real commands reject unknown flags.
    args, extras = parser.parse_known_args(argv)
    if extras and not getattr(args, "stub", False):
        parser.error(f"unrecognized arguments: {' '.join(extras)}")
    return int(args.func(args, extras))


if __name__ == "__main__":
    sys.exit(main())
