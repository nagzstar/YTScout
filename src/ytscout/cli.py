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

from ytscout import __version__, dashboard
from ytscout.audit import COVERAGE_RELPATH, STEPS_RELPATH, AuditError, format_table, run_audit
from ytscout.collect import ChannelNotFound, Counts, collect_own
from ytscout.collect.analytics import (
    DEFAULT_DAYS,
    AnalyticsCounts,
    collect_analytics,
    window,
)
from ytscout.collect.competitors import RECENT_DAYS, CompetitorResult, collect_competitors
from ytscout.collect.discover import (
    DEFAULT_MAX_SEARCHES,
    MAX_SEARCHES,
    OWN_TITLES,
    SEARCH_ORDERS,
    DiscoveryResult,
    discover,
    queries_within,
    worst_case_units,
)
from ytscout.dashboard import serve as serve_mod
from ytscout.score import WINDOWS, score_competitors
from ytscout.scoring import (
    SCORING_RELPATH,
    ScoringConfigError,
    discovery_config,
    load_scoring,
    metrics_config,
    shorts_max_seconds,
)
from ytscout.scoring.metrics import FORMATS
from ytscout.settings import (
    DEFAULT_DATA_DIR,
    DEFAULT_QUOTA_DAILY_CAP,
    Settings,
    SettingsError,
    SettingsMissing,
    find_repo_root,
    load,
)
from ytscout.store import DB_FILENAME, connect, default_db_path, read_copy, repo, utc_now
from ytscout.youtube import (
    DataApi,
    DryRunTransport,
    GoogleTransport,
    Ledger,
    Transport,
)
from ytscout.youtube.analytics import (
    AnalyticsApi,
    AnalyticsTransport,
    DryRunAnalyticsTransport,
    GoogleAnalyticsTransport,
    QueryRejected,
)
from ytscout.youtube.client import PAGE_SIZE
from ytscout.youtube.oauth import (
    TokenError,
    check_scopes,
    describe,
    load_credentials,
    run_consent_flow,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_QUOTA_EXHAUSTED = 3
EXIT_NO_OAUTH_TOKEN = 4
EXIT_CLAUDE_UNAVAILABLE = 5

# command -> (help text, issue that delivers it). Order is the order shown in --help.
STUBS: dict[str, tuple[str, str]] = {
    "packet": ("write one analysis packet for Claude", "016"),
    "analyse": ("run Claude analyses over packets", "017"),
    "scout": ("the niche pipeline: propose / validate / tag / snowball / sensitivity", "021"),
}

COMMANDS: tuple[str, ...] = (
    "doctor",
    "auth",
    "collect",
    "discover",
    "dashboard",
    "serve",
    "score",
    *STUBS,
    "audit",
)

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

    auth = sub.add_parser(
        "auth", help="OAuth consent for the Analytics API (opens a browser; Active step)"
    )
    auth.add_argument(
        "--status",
        action="store_true",
        help="report the saved token: present, refreshable, scope verdict (no values)",
    )
    auth.set_defaults(func=cmd_auth)

    collect = sub.add_parser(
        "collect", help="pull own/competitor/analytics/transcript/niche data into SQLite"
    )
    collect.add_argument(
        "--own", action="store_true", help="the own channel: metadata, snapshot, newest uploads"
    )
    collect.add_argument(
        "--competitors",
        action="store_true",
        help="weekly refresh of the own channel and every approved/watch competitor",
    )
    collect.add_argument(
        "--analytics",
        action="store_true",
        help="own-channel YouTube Analytics (OAuth): per-video totals, per-day, traffic sources",
    )
    collect.add_argument(
        "--days",
        type=_positive_int,
        default=DEFAULT_DAYS,
        help=f"--analytics: window length in days, ending yesterday (default {DEFAULT_DAYS})",
    )
    collect.add_argument(
        "--videos",
        type=_positive_int,
        default=DEFAULT_OWN_VIDEOS,
        help=f"--own: newest uploads to collect (default {DEFAULT_OWN_VIDEOS})",
    )
    collect.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted run from its checkpoint (issue 032; currently a no-op)",
    )
    _add_quota_flags(collect)
    collect.set_defaults(func=cmd_collect)

    disc = sub.add_parser(
        "discover", help="find candidate competitors from the own channel's titles (monthly)"
    )
    disc.add_argument(
        "--max-searches",
        type=int,
        default=DEFAULT_MAX_SEARCHES,
        help=f"search.list calls, 100 units each; 2 per query (default {DEFAULT_MAX_SEARCHES},"
        f" at most {MAX_SEARCHES})",
    )
    _add_quota_flags(disc)
    disc.set_defaults(func=cmd_discover)

    score = sub.add_parser("score", help="compute metrics and scores from the DB (no API)")
    score.add_argument(
        "--competitors",
        action="store_true",
        help="channel metrics for every tracked channel: 90d/365d x shorts/longform",
    )
    score.set_defaults(func=cmd_score)

    dash = sub.add_parser(
        "dashboard", help="build the static dashboard HTML from the DB (no API, no network)"
    )
    dash.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"output file (default: <repo root>/{dashboard.DEFAULT_OUT_RELPATH.as_posix()})",
    )
    dash.set_defaults(func=cmd_dashboard)

    serve = sub.add_parser(
        "serve", help="localhost review server: approve/reject/watch from the dashboard"
    )
    serve.add_argument(
        "--port",
        type=int,
        default=serve_mod.DEFAULT_PORT,
        help=f"port on 127.0.0.1 (default {serve_mod.DEFAULT_PORT})",
    )
    serve.set_defaults(func=cmd_serve)

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
    """``collect --own`` or ``--competitors``: channels → uploads → videos → snapshots.

    Exit 3 when a quota cap stops the run; everything collected before it is committed.
    """
    if [args.own, args.competitors, args.analytics].count(True) != 1:
        print(
            "ytscout collect: choose one source: --own, --competitors or --analytics",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if args.analytics:
        # Analytics quota is separate from the Data API ledger: no --max-units needed.
        return _collect_analytics(args)
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

    if args.competitors:
        return _collect_competitors(args, channel_id, daily_cap, db_path, transport, shorts_max)

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


def make_analytics_transport(credentials: object, dry_run: bool) -> AnalyticsTransport:
    """The Analytics transport. Tests replace this with a ``FakeAnalyticsTransport``."""
    if dry_run:
        return DryRunAnalyticsTransport()
    return GoogleAnalyticsTransport(credentials)


def _auth_hint(settings: Settings) -> str:
    return (
        "run `ytscout auth` (an Active step: it opens Google's consent screen) to save a "
        f"read-only Analytics token at {_relative_to_root(settings)(settings.token_path)}"
    )


def _own_video_ids(conn: sqlite3.Connection | None, channel_id: str) -> list[str]:
    if conn is None:
        return []
    try:
        return [row["id"] for row in repo.videos_for_channel(conn, channel_id)]
    except sqlite3.Error as exc:
        print(f"note: could not read own videos: {exc}", file=sys.stderr)
        return []


def _print_analytics_plan(transport: DryRunAnalyticsTransport, n_videos: int) -> None:
    print(
        "dry run: planned YouTube Analytics API queries (nothing is sent, nothing is "
        "written; Analytics quota is separate from the Data API ledger)"
    )
    for params in transport.calls:
        shown = dict(params)
        filters = str(shown.get("filters", ""))
        if n_videos and filters.startswith("video=="):
            shown["filters"] = f"video==<{filters.count(',') + 1} id(s)>"
        print("  reports.query " + " ".join(f"{k}={v}" for k, v in shown.items()))
    print(f"planned: {len(transport.calls)} queries for {n_videos} own video(s) in the DB")
    print(
        "if the API rejects impressions,impressionsClickThroughRate, the first video batch "
        "is retried once without them and later batches do not ask"
    )


def _print_analytics_summary(counts: AnalyticsCounts, start: str, end: str) -> None:
    impressions = {None: "not asked", True: "available", False: "rejected by the API (null)"}
    print(
        f"collect --analytics {start}..{end}: videos {counts.videos}, days {counts.days}, "
        f"traffic sources {counts.traffic_sources}, queries {counts.queries}; "
        f"impressions/CTR {impressions[counts.impressions_available]}"
    )


def _collect_analytics(args: argparse.Namespace) -> int:
    """``collect --analytics``: exit 4 without a fit token; a dry run needs none."""
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

    channel_id = settings.own_channel_id if settings else DRY_RUN_CHANNEL_ID
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME
    start, end = window(args.days, utc_now().date())

    if args.dry_run:
        with _read_only(db_path) as real:
            ids = _own_video_ids(real, channel_id)
        transport = make_analytics_transport(None, True)
        conn = connect(Path(":memory:"))
        try:
            collect_analytics(
                AnalyticsApi(transport),
                conn,
                ids or ["<own video ids from collect --own>"],
                start=start,
                end=end,
            )
        finally:
            conn.close()
        if isinstance(transport, DryRunAnalyticsTransport):
            _print_analytics_plan(transport, len(ids))
        return EXIT_OK

    assert settings is not None
    try:
        credentials = load_credentials(settings.token_path)
    except TokenError as exc:
        print(f"collect: {exc}; {_auth_hint(settings)}", file=sys.stderr)
        return EXIT_NO_OAUTH_TOKEN
    if credentials is None:
        print(f"collect: no OAuth token; {_auth_hint(settings)}", file=sys.stderr)
        return EXIT_NO_OAUTH_TOKEN
    problems = check_scopes(credentials)
    if problems:
        print(
            f"collect: the OAuth token is not fit for use ({'; '.join(problems)}); "
            f"{_auth_hint(settings)}",
            file=sys.stderr,
        )
        return EXIT_NO_OAUTH_TOKEN

    conn = connect(db_path)
    try:
        ids = _own_video_ids(conn, channel_id)
        if not ids:
            print(
                "note: no own videos in the DB yet (run collect --own first); "
                "collecting channel-level rows only",
                file=sys.stderr,
            )
        api = AnalyticsApi(make_analytics_transport(credentials, False))
        with recorded_run(conn, "collect_analytics") as run:
            try:
                counts = collect_analytics(api, conn, ids, start=start, end=end)
            except QueryRejected as exc:
                run.status = "error"
                print(f"collect: the Analytics API rejected a query: {exc}", file=sys.stderr)
                return EXIT_ERROR
        _print_analytics_summary(counts, start, end)
        return EXIT_OK
    finally:
        conn.close()


def cmd_auth(args: argparse.Namespace, _extras: list[str]) -> int:
    """``auth``: the consent flow. ``auth --status``: report on the saved token, no values."""
    try:
        settings = load()
    except SettingsError as exc:
        print(f"auth: {exc}", file=sys.stderr)
        return EXIT_ERROR
    rel = _relative_to_root(settings)
    if args.status:
        status = describe(settings.token_path)
        print(f"token: {'present' if status.present else 'absent'} ({rel(settings.token_path)})")
        if not status.present:
            print("run `ytscout auth` to create one")
            return EXIT_OK
        if status.error:
            print(f"loads: no - {status.error}")
            print("run `ytscout auth` to replace it")
            return EXIT_OK
        print(f"expired: {_yes_no(status.expired)}")
        print(f"refresh token: {_yes_no(status.has_refresh_token)}")
        if status.refreshed is True:
            print("refresh: ok (refreshed and saved)")
        elif status.refreshed is False:
            print(f"refresh: failed - {status.refresh_error}")
        elif status.expired:
            print("refresh: impossible without a refresh token")
        else:
            print("refresh: not needed yet")
        print("scopes: " + (", ".join(status.scopes) or "(none recorded)"))
        if status.problems:
            print("scope verdict: NOT FIT for collect --analytics")
            for problem in status.problems:
                print(f"  - {problem}")
            print("run `ytscout auth` to replace the token")
        else:
            print("scope verdict: ok")
        return EXIT_OK

    if not settings.client_secret_path.is_file():
        print(
            f"auth: no OAuth client secret at {rel(settings.client_secret_path)} "
            "(set YT_CLIENT_SECRET_PATH in .env)",
            file=sys.stderr,
        )
        return EXIT_ERROR
    credentials = run_consent_flow(settings.client_secret_path, settings.token_path)
    print(f"token saved to {rel(settings.token_path)}")
    problems = check_scopes(credentials)
    if problems:
        print("scope verdict: NOT FIT - " + "; ".join(problems), file=sys.stderr)
        return EXIT_ERROR
    print("scope verdict: ok")
    return EXIT_OK


def _tracked(conn: sqlite3.Connection | None) -> list[dict]:
    """Tracked channel rows as dicts; none when the DB is absent or predates the schema."""
    if conn is None:
        return []
    try:
        return [dict(row) for row in repo.tracked_channels(conn)]
    except sqlite3.Error as exc:
        print(f"note: could not read tracked channels: {exc}", file=sys.stderr)
        return []


def _print_competitor_plan(result: CompetitorResult, transport: DryRunTransport) -> None:
    print("dry run: planned YouTube Data API calls (nothing is sent, nothing is written)")
    print(
        f"  channels.list x {result.channel_batches} (50 ids per call)"
        f" - {result.channel_units} unit(s)"
    )
    for r in result.reports:
        name = f"{r.channel_id} ({r.title})" if r.title else r.channel_id
        print(
            f"  {name}: playlistItems.list x {r.pages}, videos.list x {r.video_batches}"
            f" - {r.units} unit(s)"
        )
    print(
        f"planned: {len(transport.calls)} calls, {transport.units} units for "
        f"{len(result.reports)} tracked channel(s)"
    )
    print(
        "each channel pages on until a page's oldest video is known and older than "
        f"{RECENT_DAYS} days; a channel's first refresh walks its whole playlist"
    )


def _print_competitor_summary(result: CompetitorResult, ledger: Ledger) -> None:
    for r in result.reports:
        print(
            f"  {r.channel_id} {r.title or ''}: {r.pages} page(s), {r.videos} video(s),"
            f" {r.units} unit(s); {r.stop}"
        )
    c = result.counts
    print(
        f"collect --competitors: channels {c.channels}, videos {c.videos}, snapshots "
        f"{c.snapshots} ({c.channel_snapshots} channel, {c.video_snapshots} video); "
        f"units this run {ledger.run_used}, units today {ledger.used_today()}"
    )


def _collect_competitors(
    args: argparse.Namespace,
    own_channel_id: str,
    daily_cap: int,
    db_path: Path,
    transport: Transport,
    shorts_max: int,
) -> int:
    """``collect --competitors``; exit 3 when a quota cap stops it (batches so far kept)."""
    now = utc_now()
    if args.dry_run:
        with _read_only(db_path) as real:
            channels = _tracked(real)
        conn = _dry_run_connection(db_path)
        try:
            ledger = Ledger(conn, daily_cap, run_cap=args.max_units, dry_run=True)
            result = collect_competitors(
                DataApi(ledger, transport),
                conn,
                channels,
                own_channel_id=own_channel_id,
                shorts_max_seconds=shorts_max,
                now=now,
            )
        finally:
            conn.close()
        if isinstance(transport, DryRunTransport):
            _print_competitor_plan(result, transport)
        if result.stopped is not None:
            print(f"a real run would stop here: {result.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK

    conn = connect(db_path)
    try:
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        with recorded_run(conn, "collect_competitors") as run:
            result = collect_competitors(
                DataApi(ledger, transport),
                conn,
                [dict(row) for row in repo.tracked_channels(conn)],
                own_channel_id=own_channel_id,
                shorts_max_seconds=shorts_max,
                now=now,
            )
            if result.stopped is not None:
                run.status = "quota_exhausted"
        _print_competitor_summary(result, ledger)
        if result.stopped is not None:
            print(
                f"stopped early; everything above is committed: {result.stopped}", file=sys.stderr
            )
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def cmd_score(args: argparse.Namespace, _extras: list[str]) -> int:
    """``score --competitors``: append channel_metrics rows. No API calls."""
    if not args.competitors:
        print(
            "ytscout score: choose what to score: --competitors (niche scores: issue 024)",
            file=sys.stderr,
        )
        return EXIT_ERROR
    found = _dashboard_db_path("score")
    if found is None:
        return EXIT_ERROR
    root, db_path = found
    try:
        cfg = metrics_config(load_scoring(root / SCORING_RELPATH))
    except ScoringConfigError as exc:
        print(f"score: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not db_path.is_file():
        print(f"score: no database at {db_path}; run `collect` first", file=sys.stderr)
        return EXIT_ERROR
    conn = connect(db_path)
    try:
        with recorded_run(conn, "score_competitors"):
            result = score_competitors(conn, config=cfg, now=utc_now())
    finally:
        conn.close()
    print(
        f"score --competitors: {result.rows} channel_metrics rows for {result.channels}"
        f" channel(s) x {len(WINDOWS)} windows x {len(FORMATS)} formats"
        f" (computed_at {result.computed_at})"
    )
    return EXIT_OK


def _discovery_inputs(
    conn: sqlite3.Connection | None, own_channel_id: str
) -> tuple[list[str], set[str], int | None]:
    """``(own titles, rejected channel ids, own subs from the latest snapshot)``."""
    if conn is None:
        return [], set(), None
    titles = repo.recent_titles(conn, own_channel_id, OWN_TITLES)
    rejected = repo.channel_ids_with_status(conn, "rejected")
    snap = repo.latest_channel_snapshot(conn, own_channel_id)
    return titles, rejected, snap["subs"] if snap else None


@contextmanager
def _read_only(db_path: Path) -> Iterator[sqlite3.Connection | None]:
    """The real DB opened ``mode=ro``, or ``None`` if it does not exist or will not open."""
    conn: sqlite3.Connection | None = None
    if db_path.is_file():
        try:
            conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            print(f"note: could not open {db_path} read-only: {exc}", file=sys.stderr)
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()


def _print_discovery_plan(result: DiscoveryResult, transport: DryRunTransport) -> None:
    print("dry run: planned YouTube Data API calls (nothing is sent, nothing is written)")
    print(f"queries ({len(result.queries)}):")
    for query in result.queries:
        print(f"  {query}")
    searches = sum(1 for call in transport.calls if call[0] == "search")
    print(
        f"planned: {searches} searches ({searches * 100} units) + 1 own channels.list"
        f" = {transport.units} units"
    )
    print(
        "channels.list and videos.list for the hits follow at 1 unit per 50 ids;"
        f" worst case for this plan: {worst_case_units(len(result.queries))} units"
    )


def _print_discovery(result: DiscoveryResult, ledger: Ledger) -> None:
    print(f"queries ({len(result.queries)}): {'; '.join(result.queries)}")
    print(
        f"discover: {result.searches} searches, {len(result.candidates)} hit channels, "
        f"{len(result.kept)} kept, {len(result.dropped)} dropped; "
        f"units this run {ledger.run_used}, units today {ledger.used_today()}"
    )
    for v in result.kept:
        item = result.candidates[v.channel_id].item or {}
        title = (item.get("snippet") or {}).get("title") or ""
        print(f"  + {v.channel_id} score {v.score} {title}: {'; '.join(v.reasons)}")
    for v in result.dropped:
        print(f"  - {v.channel_id}: {'; '.join(v.reasons)}")


def cmd_discover(args: argparse.Namespace, _extras: list[str]) -> int:
    """``discover``: seed queries → searches → channel and video lookups → filter → write.

    Exit 3 when a quota cap stops the run; nothing is written for channels not yet judged.
    """
    if not 2 <= args.max_searches <= MAX_SEARCHES:
        print(
            f"ytscout discover: --max-searches must be 2 to {MAX_SEARCHES} (2 per query,"
            f" 100 units each), got {args.max_searches}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if not _require_quota_flag(args, "discover"):
        return EXIT_ERROR

    root = find_repo_root()
    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"discover: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; planning with a placeholder channel id")
        settings = None
    except SettingsError as exc:
        print(f"discover: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        scoring = load_scoring(root / SCORING_RELPATH)
        shorts_max = shorts_max_seconds(scoring)
        cfg = discovery_config(scoring)
    except ScoringConfigError as exc:
        print(f"discover: {exc}", file=sys.stderr)
        return EXIT_ERROR

    wanted = args.max_searches // len(SEARCH_ORDERS)
    max_queries = queries_within(args.max_units, wanted)
    if max_queries == 0:
        print(
            f"discover: --max-units {args.max_units} is too small; one query can cost up to "
            f"{worst_case_units(1)} units",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if max_queries < wanted:
        print(f"note: --max-units {args.max_units} allows {max_queries} of {wanted} queries")

    channel_id = settings.own_channel_id if settings else DRY_RUN_CHANNEL_ID
    daily_cap = settings.quota.daily_cap if settings else DEFAULT_QUOTA_DAILY_CAP
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME
    try:
        transport = make_transport(settings, args.dry_run)
    except ValueError as exc:
        print(f"discover: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.dry_run:
        with _read_only(db_path) as real:
            titles, rejected, own_subs = _discovery_inputs(real, channel_id)
        if not titles:
            print("note: no own-channel titles in the DB yet; run `collect --own` first")
        conn = _dry_run_connection(db_path)
        try:
            ledger = Ledger(conn, daily_cap, run_cap=args.max_units, dry_run=True)
            result = discover(
                DataApi(ledger, transport),
                conn,
                channel_id,
                titles=titles,
                rejected=rejected,
                own_subs_fallback=own_subs,
                max_queries=max_queries,
                cfg=cfg,
                shorts_max_seconds=shorts_max,
            )
        finally:
            conn.close()
        if isinstance(transport, DryRunTransport):
            _print_discovery_plan(result, transport)
        if result.stopped is not None:
            print(f"a real run would stop here: {result.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK

    conn = connect(db_path)
    try:
        titles, rejected, own_subs = _discovery_inputs(conn, channel_id)
        if not titles:
            print(
                "discover: no own-channel titles in the DB; run `collect --own` first",
                file=sys.stderr,
            )
            return EXIT_ERROR
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        with recorded_run(conn, "discover") as run:
            result = discover(
                DataApi(ledger, transport),
                conn,
                channel_id,
                titles=titles,
                rejected=rejected,
                own_subs_fallback=own_subs,
                max_queries=max_queries,
                cfg=cfg,
                shorts_max_seconds=shorts_max,
            )
            if result.stopped is not None:
                run.status = "quota_exhausted"
        _print_discovery(result, ledger)
        if result.stopped is not None:
            print(f"stopped early; nothing unjudged was written: {result.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def _dashboard_db_path(command: str) -> tuple[Path, Path] | None:
    """(repo root, DB path) from the settings, or the default when there are none yet."""
    root = find_repo_root()
    try:
        settings: Settings | None = load(repo_root=root)
    except SettingsMissing:
        settings = None
    except SettingsError as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return None
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME
    return root, db_path


def cmd_serve(args: argparse.Namespace, _extras: list[str]) -> int:
    """Serve the dashboard on 127.0.0.1 and record approve/reject/watch clicks until Ctrl-C."""
    found = _dashboard_db_path("serve")
    if found is None:
        return EXIT_ERROR
    root, db_path = found
    out = root / dashboard.DEFAULT_OUT_RELPATH
    try:
        server = serve_mod.make_server(
            db_path, out, db_path.parent / serve_mod.DECISIONS_FILENAME, port=args.port
        )
    except OSError as exc:
        print(f"serve: cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        server.rebuild()
        print(f"serve: reviewing {db_path}")
        print(f"serve: open {server.url}  (Ctrl-C to stop)", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve: stopped")
    finally:
        server.server_close()
    return EXIT_OK


def cmd_dashboard(args: argparse.Namespace, _extras: list[str]) -> int:
    """Render the dashboard from a read-only copy of the DB; an absent DB renders empty."""
    found = _dashboard_db_path("dashboard")
    if found is None:
        return EXIT_ERROR
    root, db_path = found
    out = args.out if args.out is not None else root / dashboard.DEFAULT_OUT_RELPATH
    if not db_path.is_file():
        print(f"note: no database at {db_path}; building an empty dashboard")
    try:
        conn = read_copy(db_path)
    except sqlite3.Error as exc:
        print(f"dashboard: could not read {db_path}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        dash = dashboard.build(conn, out)
    finally:
        conn.close()
    print(
        f"dashboard: wrote {out} ({out.stat().st_size:,} bytes; {len(dash.own_videos)} own "
        f"videos, {len(dash.candidates)} candidates, {len(dash.approved)} approved)"
    )
    return EXIT_OK


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
