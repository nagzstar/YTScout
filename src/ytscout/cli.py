"""Command-line entry point: ``python -m ytscout <command>``.

Exit codes (DESIGN.md §9.2): 0 ok · 1 error · 2 not implemented yet · 3 quota exhausted ·
4 no OAuth token · 5 ``claude`` unavailable. Stubs exit 2 so ``scripts/run_weekly.ps1``
can skip steps that no issue has delivered yet.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import traceback
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ytscout import __version__, claude_runner, dashboard, packets, transcripts
from ytscout import doctor as doctor_mod
from ytscout.analyse import DEFAULT_LIMIT as DEFAULT_SUMMARY_LIMIT
from ytscout.analyse import (
    analyse_competitors,
    competitor_paths,
    record_pending,
    summarise_videos,
    summary_paths,
)
from ytscout.audit import (
    COVERAGE_RELPATH,
    STEPS_RELPATH,
    AuditError,
    format_table,
    load_coverage,
    load_steps,
    run_audit,
)
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
from ytscout.collect.niches import NicheRefreshResult, collect_niches, tracked_niches
from ytscout.collect.resume import (
    KIND_COMPETITORS,
    KIND_NICHES,
    KIND_OWN,
    KIND_VALIDATE,
    Checkpoint,
    done_keys,
    iso_week,
    open_checkpoint,
    resume_hint,
    validate_run_id,
)
from ytscout.collect.transcripts import DEFAULT_LIMIT as DEFAULT_TRANSCRIPT_LIMIT
from ytscout.collect.transcripts import collect_transcripts
from ytscout.dashboard import serve as serve_mod
from ytscout.score import WINDOWS, score_competitors
from ytscout.scoring import (
    SCORING_RELPATH,
    ScoringConfigError,
    discovery_config,
    load_scoring,
    metrics_config,
    niche_scoring_config,
    relevance_config,
    shorts_max_seconds,
    summaries_per_channel,
    validation_config,
)
from ytscout.scoring.metrics import FORMATS
from ytscout.scout import propose as scout_propose
from ytscout.scout import score as scout_score
from ytscout.scout import snowball as scout_snowball
from ytscout.scout import tag as scout_tag
from ytscout.scout import validate as scout_validate
from ytscout.settings import (
    DEFAULT_DATA_DIR,
    DEFAULT_QUOTA_DAILY_CAP,
    DEFAULT_USD_GBP,
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
    tls,
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
# Empty since 021: every top-level command is real. Kept so a future command can stub in.
STUBS: dict[str, tuple[str, str]] = {}

# scout subcommand -> (help text, issue that delivers it); these exit 2 until then.
SCOUT_STUBS: dict[str, tuple[str, str]] = {
    "sensitivity": ("re-score five niches under threshold changes", "028"),
}

COMMANDS: tuple[str, ...] = (
    "doctor",
    "auth",
    "collect",
    "discover",
    "dashboard",
    "serve",
    "decide",
    "score",
    "packet",
    "analyse",
    "scout",
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

    doctor = sub.add_parser(
        "doctor", help="check every dependency; prints no secret (1 unit, 1 claude -p call)"
    )
    doctor.add_argument(
        "--offline",
        action="store_true",
        help="skip the Data API, OAuth token and claude login probes (no units, no claude -p)",
    )
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
        "--transcripts",
        action="store_true",
        help="transcripts of own and approved/watch videos not yet fetched (no Data API units)",
    )
    collect.add_argument(
        "--niches",
        action="store_true",
        help="weekly refresh of every tracked niche's channels (no searches), then re-score them",
    )
    collect.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_TRANSCRIPT_LIMIT,
        help=f"--transcripts: videos to try this run (default {DEFAULT_TRANSCRIPT_LIMIT})",
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
        help="--own/--competitors/--niches: skip what this ISO week's run already finished"
        " (a quota stop's checkpoint); --analytics and --transcripts ignore it",
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
    what = score.add_mutually_exclusive_group()
    what.add_argument(
        "--competitors",
        action="store_true",
        help="channel metrics for every tracked channel: 90d/365d x shorts/longform",
    )
    what.add_argument(
        "--niches",
        action="store_true",
        help="niche scores for every tagged validated/scored/tracked niche, ranked",
    )
    what.add_argument("--all", action="store_true", help="competitors, then niches (the default)")
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
        "serve",
        help="localhost review server: approve/reject/watch from the dashboard; "
        "templates reload on refresh, Python code changes need restart",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=serve_mod.DEFAULT_PORT,
        help=f"port on 127.0.0.1 (default {serve_mod.DEFAULT_PORT})",
    )
    serve.set_defaults(func=cmd_serve)

    decide = sub.add_parser(
        "decide",
        help="bulk approve/reject/watch/undecide competitors, with an audit line (no network)",
    )
    target = decide.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--channel",
        action="append",
        metavar="ID",
        help="a competitor channel id (repeatable)",
    )
    target.add_argument(
        "--where",
        metavar="EXPR",
        help='SQL filter over id, title, status, subs, e.g. "subs < 10000 and status is null"',
    )
    decide.add_argument(
        "--set",
        dest="decision",
        required=True,
        choices=repo.DECISIONS["channel"],
        help="the decision; undecided puts a channel back in Candidates",
    )
    decide.add_argument(
        "--dry-run", action="store_true", help="list the rows it would change; write nothing"
    )
    decide.set_defaults(func=cmd_decide)

    packet = sub.add_parser(
        "packet", help="write one analysis packet for Claude and print its path (debugging)"
    )
    packet.add_argument("--video", required=True, metavar="ID", help="video id to pack")
    packet.set_defaults(func=cmd_packet)

    analyse = sub.add_parser("analyse", help="run Claude analyses over packets (claude -p)")
    analyse.add_argument(
        "--summaries",
        action="store_true",
        help="one per-video summary call for each video with a transcript attempt and no "
        "summary under the current prompt hash",
    )
    analyse.add_argument(
        "--competitors", action="store_true", help="the competitor comparison call (issue 017)"
    )
    analyse.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_SUMMARY_LIMIT,
        help=f"--summaries: videos to summarise this run (default {DEFAULT_SUMMARY_LIMIT})",
    )
    analyse.add_argument(
        "--per-channel",
        type=_positive_int,
        default=None,
        metavar="N",
        help="--summaries: at most N videos per channel this run "
        "(default video_summaries.per_channel in config/scoring.yaml)",
    )
    analyse.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be summarised and the command shape; call nothing, write nothing",
    )
    analyse.set_defaults(func=cmd_analyse)

    scout = sub.add_parser(
        "scout", help="the niche pipeline: propose / validate / tag / snowball / sensitivity"
    )
    scout_sub = scout.add_subparsers(dest="scout_command", metavar="<subcommand>", required=True)
    propose = scout_sub.add_parser(
        "propose",
        help="add candidate niches: the Claude brainstorm (claude -p) or --from-seeds (no API)",
    )
    propose.add_argument(
        "--count",
        type=_positive_int,
        default=scout_propose.DEFAULT_COUNT,
        help=f"niches to ask the brainstorm for (default {scout_propose.DEFAULT_COUNT})",
    )
    propose.add_argument(
        "--from-seeds",
        action="store_true",
        help="load config/seed_niches.yaml instead of running the brainstorm",
    )
    propose.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be added or asked; call nothing, write nothing",
    )
    propose.set_defaults(func=cmd_scout_propose)
    validate = scout_sub.add_parser(
        "validate",
        help="search a niche's queries and sample its channels and videos (up to ~762 units"
        " per niche)",
    )
    which = validate.add_mutually_exclusive_group(required=True)
    which.add_argument("--niche", type=_positive_int, metavar="ID", help="validate one niche")
    which.add_argument(
        "--all-proposed",
        action="store_true",
        help="validate every proposed niche, oldest first, until the quota runs out",
    )
    validate.add_argument(
        "--resume",
        action="store_true",
        help="skip niches already validated in the last 10 days (their checkpoint)",
    )
    _add_quota_flags(validate)
    validate.set_defaults(func=cmd_scout_validate)
    tag = scout_sub.add_parser(
        "tag",
        help=f"Claude tags each validated niche's required production steps (claude -p,"
        f" {scout_tag.BATCH_SIZE} niches per call)",
    )
    tag.add_argument(
        "--limit",
        type=_positive_int,
        default=scout_tag.DEFAULT_LIMIT,
        help=f"niches to tag at most (default {scout_tag.DEFAULT_LIMIT})",
    )
    tag.add_argument(
        "--dry-run",
        action="store_true",
        help="show which niches would be tagged in which batches; call nothing, write nothing",
    )
    tag.set_defaults(func=cmd_scout_tag)
    snowball = scout_sub.add_parser(
        "snowball",
        help="propose adjacent niches from known channels' top titles (100 units per search)",
    )
    snowball.add_argument(
        "--max-searches",
        type=_positive_int,
        default=scout_snowball.DEFAULT_MAX_SEARCHES,
        help=f"search.list calls at most (default {scout_snowball.DEFAULT_MAX_SEARCHES},"
        f" refused above {scout_snowball.MAX_SEARCHES_CAP})",
    )
    _add_quota_flags(snowball)
    snowball.set_defaults(func=cmd_scout_snowball)
    for name, (help_text, issue) in SCOUT_STUBS.items():
        stub = scout_sub.add_parser(name, help=f"{help_text} (issue {issue})")
        stub.set_defaults(func=_make_stub(f"scout {name}", issue), stub=True)

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


def _print_resume(args: argparse.Namespace, checkpoint: Checkpoint, noun: str) -> None:
    """What ``--resume`` skipped (or would skip, on a dry run)."""
    if not args.resume:
        return
    verb = "would skip" if args.dry_run else "skipping"
    if checkpoint.skipped:
        print(
            f"resume: {verb} {len(checkpoint.skipped)} {noun}(s) already done in"
            f" {checkpoint.run_id}: {', '.join(checkpoint.skipped)}"
        )
    else:
        print(f"resume: nothing done yet in {checkpoint.run_id}; running in full")


def _print_resume_hint(args: argparse.Namespace) -> None:
    print(resume_hint(getattr(args, "argv", None) or []), file=sys.stderr)


# run_weekly.ps1 sets this to its log file so each step's ``runs`` row can point at it.
RUN_LOG_ENV = "YTSCOUT_RUN_LOG"
ERROR_TAIL_CHARS = 500


@dataclass
class RunRecord:
    """The ``runs`` row a command is writing; set ``status`` before the block ends."""

    id: int
    status: str = "ok"


@contextmanager
def recorded_run(conn: sqlite3.Connection, kind: str) -> Iterator[RunRecord]:
    """Record a ``runs`` row around a DB-touching command: start, finish, kind, status,
    and what it spent (031): ledger units, Claude calls, tokens and the cost estimate.

    An exception escaping the block marks the row ``error``, keeps the last 500 characters
    of its traceback in ``error_tail``, and propagates.
    """
    units_before = repo.quota_total(conn)
    usage_before = claude_runner.snapshot()
    with conn:
        record = RunRecord(repo.start_run(conn, kind, os.environ.get(RUN_LOG_ENV) or None))
    error_tail: str | None = None
    try:
        yield record
    except BaseException:
        record.status = "error"
        error_tail = traceback.format_exc()[-ERROR_TAIL_CHARS:]
        raise
    finally:
        usage = claude_runner.USAGE.since(usage_before)
        with conn:
            repo.finish_run(
                conn,
                record.id,
                record.status,
                units_used=repo.quota_total(conn) - units_before,
                claude_calls=usage.calls,
                claude_input_tokens=usage.input_tokens,
                claude_output_tokens=usage.output_tokens,
                claude_cost_usd_est=usage.cost_usd_est,
                error_tail=error_tail,
            )


def _ca_certs_if_token(settings: Settings) -> Path | None:
    """The CA bundle for a token refresh, or ``None`` when there is no token to refresh.

    Building the bundle creates ``data/``; a run that stops for want of a token must not.
    """
    return tls.ca_bundle(settings.data_dir).path if settings.token_path.is_file() else None


def make_transport(settings: Settings | None, dry_run: bool) -> Transport:
    """The Data API transport for a command. Tests replace this with a ``FakeTransport``."""
    if dry_run:
        return DryRunTransport()
    assert settings is not None
    return GoogleTransport(settings.api_key, ca_certs=tls.ca_bundle(settings.data_dir).path)


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
    """``collect --own``, ``--competitors`` or ``--niches``: channels → uploads → videos →
    snapshots (plus Analytics and transcripts).

    Exit 3 when a quota cap stops the run; everything collected before it is committed.
    """
    sources = [args.own, args.competitors, args.analytics, args.transcripts, args.niches]
    if sources.count(True) != 1:
        print(
            "ytscout collect: choose one source: --own, --competitors, --analytics, "
            "--transcripts or --niches",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if args.transcripts:
        # youtube-transcript-api does not use the Data API quota: no --max-units needed.
        return _collect_transcripts(args)
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
    if args.niches:
        return _collect_niches(args, root, daily_cap, db_path, transport, shorts_max)

    now = utc_now()
    if args.dry_run:
        conn = _dry_run_connection(db_path)
        try:
            with _read_only(db_path) as real:
                checkpoint = open_checkpoint(
                    conn, KIND_OWN, iso_week(now), now, resume=args.resume, state=real
                )
            ledger = Ledger(conn, daily_cap, run_cap=args.max_units, dry_run=True)
            counts = collect_own(
                DataApi(ledger, transport),
                conn,
                channel_id,
                videos=args.videos,
                shorts_max_seconds=shorts_max,
                checkpoint=checkpoint,
            )
        finally:
            conn.close()
        _print_resume(args, checkpoint, "channel")
        if isinstance(transport, DryRunTransport) and not checkpoint.skipped:
            _print_plan(transport, args.videos)
        if counts.stopped is not None:
            print(f"a real run would stop here: {counts.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK

    conn = connect(db_path)
    try:
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        checkpoint = open_checkpoint(conn, KIND_OWN, iso_week(now), now, resume=args.resume)
        with recorded_run(conn, "collect_own") as run:
            try:
                counts = collect_own(
                    DataApi(ledger, transport),
                    conn,
                    channel_id,
                    videos=args.videos,
                    shorts_max_seconds=shorts_max,
                    checkpoint=checkpoint,
                )
            except ChannelNotFound as exc:
                run.status = "error"
                print(f"collect: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if counts.stopped is not None:
                run.status = "quota_exhausted"
        _print_resume(args, checkpoint, "channel")
        _print_summary(counts, ledger)
        if counts.stopped is not None:
            print(
                f"stopped early; everything above is committed: {counts.stopped}", file=sys.stderr
            )
            _print_resume_hint(args)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def make_analytics_transport(
    credentials: object, dry_run: bool, settings: Settings | None = None
) -> AnalyticsTransport:
    """The Analytics transport. Tests replace this with a ``FakeAnalyticsTransport``."""
    if dry_run:
        return DryRunAnalyticsTransport()
    assert settings is not None
    return GoogleAnalyticsTransport(credentials, ca_certs=tls.ca_bundle(settings.data_dir).path)


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
        credentials = load_credentials(settings.token_path, ca_certs=_ca_certs_if_token(settings))
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
        api = AnalyticsApi(make_analytics_transport(credentials, False, settings))
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


def _collect_transcripts(args: argparse.Namespace) -> int:
    """``collect --transcripts``: fetch missing transcripts; a dry run lists the video ids."""
    root = find_repo_root()
    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"collect: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; using the default data directory")
        settings = None
    except SettingsError as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return EXIT_ERROR
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME

    if args.dry_run:
        ids: list[str] = []
        with _read_only(db_path) as real:
            if real is not None:
                try:
                    ids = repo.transcript_candidates(real, args.limit)
                except sqlite3.Error as exc:
                    print(f"note: could not read candidates: {exc}", file=sys.stderr)
        print("dry run: transcripts that would be fetched (nothing is sent, nothing is written)")
        for video_id in ids:
            print(f"  {video_id}")
        print(f"candidates: {len(ids)}" if ids else "candidates: none")
        return EXIT_OK

    assert settings is not None
    # The library's own requests session must trust the same bundle as the Google clients.
    transcripts.configure(tls.ca_bundle(settings.data_dir).path)
    conn = connect(db_path)
    try:

        def print_result(video_id: str, status: str, detail: str | None) -> None:
            print(f"  {video_id}: {status} ({detail or '?'})", file=sys.stderr)

        with recorded_run(conn, "collect_transcripts"):
            counts = collect_transcripts(
                conn,
                limit=args.limit,
                pause_seconds=settings.transcripts.pause_seconds,
                on_result=print_result,
            )
        print(
            f"collect --transcripts: ok {counts.ok}, unavailable {counts.unavailable}, "
            f"error {counts.error}, blocked {counts.blocked}"
        )
        if counts.stopped_after is not None:
            print(
                f"collect --transcripts: stopped after {counts.stopped_after} consecutive "
                f"{counts.stopped_on or '?'}; {counts.untouched} candidates untouched"
            )
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
        status = describe(settings.token_path, ca_certs=_ca_certs_if_token(settings))
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
    credentials = run_consent_flow(
        settings.client_secret_path,
        settings.token_path,
        ca_certs=tls.ca_bundle(settings.data_dir).path,
    )
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
    run_id = iso_week(now)
    if args.dry_run:
        conn = _dry_run_connection(db_path)
        try:
            with _read_only(db_path) as real:
                channels = _tracked(real)
                checkpoint = open_checkpoint(
                    conn, KIND_COMPETITORS, run_id, now, resume=args.resume, state=real
                )
            ledger = Ledger(conn, daily_cap, run_cap=args.max_units, dry_run=True)
            result = collect_competitors(
                DataApi(ledger, transport),
                conn,
                channels,
                own_channel_id=own_channel_id,
                shorts_max_seconds=shorts_max,
                now=now,
                checkpoint=checkpoint,
            )
        finally:
            conn.close()
        _print_resume(args, checkpoint, "channel")
        if isinstance(transport, DryRunTransport):
            _print_competitor_plan(result, transport)
        if result.stopped is not None:
            print(f"a real run would stop here: {result.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK

    conn = connect(db_path)
    try:
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        checkpoint = open_checkpoint(conn, KIND_COMPETITORS, run_id, now, resume=args.resume)
        with recorded_run(conn, "collect_competitors") as run:
            result = collect_competitors(
                DataApi(ledger, transport),
                conn,
                [dict(row) for row in repo.tracked_channels(conn)],
                own_channel_id=own_channel_id,
                shorts_max_seconds=shorts_max,
                now=now,
                checkpoint=checkpoint,
            )
            if result.stopped is not None:
                run.status = "quota_exhausted"
        _print_resume(args, checkpoint, "channel")
        _print_competitor_summary(result, ledger)
        if result.stopped is not None:
            print(
                f"stopped early; everything above is committed: {result.stopped}", file=sys.stderr
            )
            _print_resume_hint(args)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def _tracked_niches(conn: sqlite3.Connection | None) -> list[dict]:
    """Tracked niches with their channels; none when the DB is absent or predates them."""
    if conn is None:
        return []
    try:
        return tracked_niches(conn)
    except sqlite3.Error as exc:
        print(f"note: could not read tracked niches: {exc}", file=sys.stderr)
        return []


def _print_niche_units(result: NicheRefreshResult) -> None:
    for r in result.reports:
        shared = f", {r.shared} shared with an earlier niche" if r.shared else ""
        state = "" if r.finished else "; stopped (quota)"
        print(
            f"  niche {r.niche_id} {r.label}: {r.channels} channel(s){shared};"
            f" channels.list x {r.channel_batches}, playlistItems.list x {r.pages},"
            f" videos.list x {r.video_batches} - {r.units} unit(s){state}"
        )


def _collect_niches(
    args: argparse.Namespace,
    root: Path,
    daily_cap: int,
    db_path: Path,
    transport: Transport,
    shorts_max: int,
) -> int:
    """``collect --niches``, then ``score`` for the niches it finished. Exit 3 on a quota
    stop (finished niches committed and scored), 1 if the re-score cannot load its config."""
    now = utc_now()
    run_id = iso_week(now)
    if args.dry_run:
        conn = _dry_run_connection(db_path)
        try:
            with _read_only(db_path) as real:
                niches = _tracked_niches(real)
                checkpoint = open_checkpoint(
                    conn, KIND_NICHES, run_id, now, resume=args.resume, state=real
                )
            ledger = Ledger(conn, daily_cap, run_cap=args.max_units, dry_run=True)
            result = collect_niches(
                DataApi(ledger, transport),
                conn,
                niches,
                shorts_max_seconds=shorts_max,
                now=now,
                checkpoint=checkpoint,
            )
        finally:
            conn.close()
        _print_resume(args, checkpoint, "niche")
        print("dry run: planned YouTube Data API calls (nothing is sent, nothing is written)")
        _print_niche_units(result)
        units = transport.units if isinstance(transport, DryRunTransport) else ledger.run_used
        print(f"planned: {units} units for {len(result.reports)} tracked niche(s); no search.list")
        print(
            "one uploads page per channel; videos.list pools the page's new and "
            f"<= {RECENT_DAYS}-day-old videos 50 ids per call (the plan assumes one call's "
            "worth per niche); then `score` re-scores each refreshed niche"
        )
        if result.stopped is not None:
            print(f"a real run would stop here: {result.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK

    conn = connect(db_path)
    try:
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        checkpoint = open_checkpoint(conn, KIND_NICHES, run_id, now, resume=args.resume)
        with recorded_run(conn, "collect_niches") as run:
            result = collect_niches(
                DataApi(ledger, transport),
                conn,
                tracked_niches(conn),
                shorts_max_seconds=shorts_max,
                now=now,
                checkpoint=checkpoint,
            )
            if result.stopped is not None:
                run.status = "quota_exhausted"
        _print_resume(args, checkpoint, "niche")
        _print_niche_units(result)
        c = result.counts
        print(
            f"collect --niches: {len(result.finished)} of {len(result.reports)} niche(s)"
            f" refreshed; channels {c.channels}, videos {c.videos}, snapshots {c.snapshots};"
            f" units this run {ledger.run_used}, units today {ledger.used_today()}"
        )
        code = EXIT_OK
        if result.finished:
            try:
                niche_cfg = niche_scoring_config(load_scoring(root / SCORING_RELPATH))
            except ScoringConfigError as exc:
                print(f"collect: {exc}", file=sys.stderr)
                return EXIT_ERROR
            code = _score_niches(conn, root, niche_cfg, niche_ids=result.finished)
        if result.stopped is not None:
            print(
                f"stopped early; finished niches are committed and scored: {result.stopped}",
                file=sys.stderr,
            )
            _print_resume_hint(args)
            return EXIT_QUOTA_EXHAUSTED
        return code
    finally:
        conn.close()


def cmd_score(args: argparse.Namespace, _extras: list[str]) -> int:
    """``score [--competitors | --niches | --all]``: append channel_metrics and/or
    niche_scores rows. No API calls. ``--all`` (the default) runs both."""
    do_competitors = args.competitors or not args.niches
    do_niches = args.niches or not args.competitors
    found = _dashboard_db_path("score")
    if found is None:
        return EXIT_ERROR
    root, db_path = found
    try:
        scoring_doc = load_scoring(root / SCORING_RELPATH)
        metrics_cfg = metrics_config(scoring_doc) if do_competitors else None
        niche_cfg = niche_scoring_config(scoring_doc) if do_niches else None
    except ScoringConfigError as exc:
        print(f"score: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not db_path.is_file():
        if args.competitors:
            print(f"score: no database at {db_path}; run `collect` first", file=sys.stderr)
            return EXIT_ERROR
        print(f"score: nothing to score (no database at {db_path})")
        return EXIT_OK
    conn = connect(db_path)
    try:
        if metrics_cfg is not None:
            with recorded_run(conn, "score_competitors"):
                result = score_competitors(conn, config=metrics_cfg, now=utc_now())
            print(
                f"score --competitors: {result.rows} channel_metrics rows for"
                f" {result.channels} channel(s) x {len(WINDOWS)} windows x {len(FORMATS)}"
                f" formats (computed_at {result.computed_at})"
            )
        if niche_cfg is not None:
            return _score_niches(conn, root, niche_cfg)
    finally:
        conn.close()
    return EXIT_OK


def _score_niches(
    conn: sqlite3.Connection, root: Path, cfg: dict, niche_ids: list[int] | None = None
) -> int:
    """``score --niches``: one niche_scores row per tagged niche (only ``niche_ids`` when
    given), then the ranked table."""
    try:
        settings: Settings | None = load(repo_root=root)
    except SettingsMissing:
        settings = None
    except SettingsError as exc:
        print(f"score: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        rpm = scout_propose.load_rpm_tiers(root / scout_propose.RPM_TIERS_RELPATH)
        steps = load_steps(root / STEPS_RELPATH)
        coverage = load_coverage(root / COVERAGE_RELPATH)
        relevance = relevance_config(load_scoring(root / SCORING_RELPATH))
    except (scout_propose.ScoutConfigError, AuditError, ScoringConfigError) as exc:
        print(f"score: {exc}", file=sys.stderr)
        return EXIT_ERROR
    with recorded_run(conn, "score_niches"):
        result = scout_score.score_niches(
            conn,
            cfg=cfg,
            rpm=rpm,
            steps=steps,
            coverage=coverage,
            usd_gbp=settings.usd_gbp if settings else DEFAULT_USD_GBP,
            now=utc_now(),
            niche_ids=niche_ids,
            relevance=relevance,
        )
    skipped = (
        f"; {result.skipped_untagged} untagged niche(s) skipped (run `scout tag`)"
        if result.skipped_untagged
        else ""
    )
    if not result.scored:
        print(f"score --niches: nothing to score{skipped}")
        return EXIT_OK
    print(scout_score.format_table(result))
    print(
        f"score --niches: {len(result.scored)} niche_scores row(s) appended"
        f" (scored_at {result.scored_at}){skipped}"
    )
    return EXIT_OK


def _discovery_inputs(
    conn: sqlite3.Connection | None, own_channel_id: str
) -> tuple[list[str], set[str]]:
    """``(own titles, rejected channel ids)``."""
    if conn is None:
        return [], set()
    titles = repo.recent_titles(conn, own_channel_id, OWN_TITLES)
    rejected = repo.channel_ids_with_status(conn, "rejected")
    return titles, rejected


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
    if result.dropped_queries:
        print(f"dropped seeds ({len(result.dropped_queries)}):")
        for query, why in result.dropped_queries:
            print(f"  {query}  ({why})")
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
            titles, rejected = _discovery_inputs(real, channel_id)
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
        titles, rejected = _discovery_inputs(conn, channel_id)
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
            db_path,
            out,
            db_path.parent / serve_mod.DECISIONS_FILENAME,
            port=args.port,
            context=dashboard.niche_context(root),
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


def cmd_decide(args: argparse.Namespace, _extras: list[str]) -> int:
    """Record one decision per matching competitor, as a dashboard click would (035)."""
    found = _dashboard_db_path("decide")
    if found is None:
        return EXIT_ERROR
    _root, db_path = found
    if not db_path.is_file():
        print(f"decide: no database at {db_path}", file=sys.stderr)
        return EXIT_ERROR
    # A dry run reads an in-memory copy, so not even a migration touches the file.
    conn = read_copy(db_path) if args.dry_run else connect(db_path)
    try:
        try:
            rows = repo.competitors_to_decide(conn, where=args.where, ids=args.channel or ())
        except sqlite3.Error as exc:
            print(f"decide: bad --where {args.where!r}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        missing = [i for i in args.channel or () if i not in {r["id"] for r in rows}]
        if missing:
            print(f"decide: no competitor {', '.join(missing)}; nothing written", file=sys.stderr)
            return EXIT_ERROR
        new = None if args.decision == repo.UNDECIDED else args.decision
        verb = "would set" if args.dry_run else "set"
        changed = 0
        for row in rows:
            subs = "-" if row["subs"] is None else f"{row['subs']:,}"
            label = f"{row['id']}  {row['title'] or '-'}  subs={subs}"
            if row["status"] == new:
                print(f"  unchanged  {label}  (already {row['status'] or 'undecided'})")
                continue
            if not args.dry_run:
                serve_mod.record(
                    conn,
                    db_path.parent / serve_mod.DECISIONS_FILENAME,
                    "channel",
                    row["id"],
                    args.decision,
                    via="cli",
                )
            print(f"  {verb}  {label}  {row['status'] or 'undecided'} -> {args.decision}")
            changed += 1
        if args.dry_run:
            print(f"decide: dry run; {changed} of {len(rows)} rows would change, nothing written")
        else:
            print(
                f"decide: {changed} of {len(rows)} rows changed, each in `decisions` and "
                f"{serve_mod.DECISIONS_FILENAME}; run `ytscout dashboard` to refresh the page"
            )
        return EXIT_OK
    finally:
        conn.close()


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
        dash = dashboard.build(conn, out, context=dashboard.niche_context(root))
    finally:
        conn.close()
    print(
        f"dashboard: wrote {out} ({out.stat().st_size:,} bytes; {len(dash.own_videos)} own "
        f"videos, {len(dash.candidates)} candidates, {len(dash.approved)} approved, "
        f"{dash.niche_counts.get('scored', 0)} niches scored)"
    )
    return EXIT_OK


def _load_settings(command: str) -> Settings | None:
    """Settings for a command that needs them; prints the problem and returns ``None``."""
    try:
        return load(repo_root=find_repo_root())
    except SettingsError as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return None


def cmd_packet(args: argparse.Namespace, _extras: list[str]) -> int:
    """``packet --video ID``: write one video packet under data/packets and print its path."""
    settings = _load_settings("packet")
    if settings is None:
        return EXIT_ERROR
    db_path = default_db_path(settings)
    if not db_path.is_file():
        print(f"packet: no database at {db_path}; run collect first", file=sys.stderr)
        return EXIT_ERROR
    conn = read_copy(db_path)  # migrated in memory; the real file is never written
    try:
        payload = packets.video_packet(conn, args.video)
    except LookupError as exc:
        print(f"packet: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        conn.close()
    path = packets.write_packet("video", payload, packets.packets_dir(settings.data_dir))
    print(path)
    return EXIT_OK


@dataclass(frozen=True)
class _SummaryOptions:
    limit: int
    per_channel: int
    outlier_multiplier: float
    outlier_window: int

    def kwargs(self) -> dict:
        return {
            "per_channel": self.per_channel,
            "outlier_multiplier": self.outlier_multiplier,
            "outlier_window": self.outlier_window,
        }


def _summary_options(args: argparse.Namespace, root: Path) -> _SummaryOptions:
    """``--limit``/``--per-channel`` plus the outlier rule from scoring.yaml (044)."""
    doc = load_scoring(root / SCORING_RELPATH)
    metrics = metrics_config(doc)
    per_channel = args.per_channel or summaries_per_channel(doc)
    return _SummaryOptions(
        args.limit, per_channel, metrics.outlier_multiplier, metrics.outlier_window_videos
    )


def _print_summary_plan(
    conn: sqlite3.Connection, prompt_path: Path, schema_path: Path, opts: _SummaryOptions
) -> None:
    prompt_hash = claude_runner.file_hash(prompt_path)
    plan = repo.summary_plan(conn, prompt_hash, opts.limit, **opts.kwargs())
    print("dry run: videos that would be summarised (claude is not called, nothing is written)")
    for c in plan:
        why = "outlier" if c.outlier else "newest"
        views = "?" if c.views is None else f"{c.views:,}"
        print(f"  {c.video_id} {c.channel_title or c.channel_id}: {views} views ({why})")
    print(f"candidates: {len(plan)}" if plan else "candidates: none")
    split: dict[str, list[int]] = {}
    for c in plan:
        counts = split.setdefault(c.channel_title or c.channel_id, [0, 0])
        counts[0] += 1
        counts[1] += c.outlier
    print(
        f"per channel (limit {opts.limit}, at most {opts.per_channel} each, "
        f"outlier >= {opts.outlier_multiplier:g}x median):"
    )
    for name, (n, n_out) in split.items():
        print(f"  {name}: {n} ({n_out} outliers)")
    reason = claude_runner.check_available()
    print(f"claude: {'ok' if reason is None else reason}")
    print(f"prompt: {prompt_path.name} ({prompt_hash}); schema: {schema_path.name}")
    argv = claude_runner.build_command(
        "claude", "<prompt text>", Path("<packet path>"), "<schema text>", None
    )
    print("command: " + " ".join(argv[:1] + [f"{argv[1]} ..."] + argv[3:]))


def _print_comparison_plan(
    conn: sqlite3.Connection, prompt_path: Path, schema_path: Path, min_age_days: int
) -> None:
    packet = packets.competitor_packet(conn, min_age_days=min_age_days)
    size = len(json.dumps(packet, ensure_ascii=False).encode("utf-8"))
    print("dry run: the competitor packet that would be compared (nothing is called or written)")
    print(f"format: {packet['format']}")
    for c in packet["channels"]:
        fresh = sum(1 for v in c["videos"] if not v["settled"])
        print(
            f"  {c['id']} {c['title'] or ''}: {len(c['videos'])} summarised "
            f"{packet['format']} ({fresh} unsettled), role {c['role']}"
        )
    print(
        f"channels: {len(packet['channels'])}; videos: {len(packets.packet_video_ids(packet))}; "
        f"packet: {size:,} bytes ({packet['meta']['videos_per_channel']} videos per channel"
        f"{', reduced' if packet['meta']['reduced'] else ''})"
    )
    reason = claude_runner.check_available()
    print(f"claude: {'ok' if reason is None else reason}")
    print(
        f"prompt: {prompt_path.name} ({claude_runner.file_hash(prompt_path)}); "
        f"schema: {schema_path.name} ({claude_runner.file_hash(schema_path)})"
    )


def cmd_analyse(args: argparse.Namespace, _extras: list[str]) -> int:
    """``analyse --summaries`` and/or ``--competitors`` through ``claude -p``; exit 5 if absent.

    With both flags the summaries run first and the comparison only if they succeeded.
    """
    if not (args.summaries or args.competitors):
        print("analyse: pass --summaries and/or --competitors", file=sys.stderr)
        return EXIT_ERROR

    root = find_repo_root()
    needed: list[Path] = []
    if args.summaries:
        needed.extend(summary_paths(root))
    if args.competitors:
        needed.extend(competitor_paths(root))
    for path in needed:
        if not path.is_file():
            print(f"analyse: missing {path}", file=sys.stderr)
            return EXIT_ERROR

    summary_opts: _SummaryOptions | None = None
    if args.summaries:
        try:
            summary_opts = _summary_options(args, root)
        except ScoringConfigError as exc:
            print(f"analyse: {exc}", file=sys.stderr)
            return EXIT_ERROR
    min_age_days = 0
    if args.competitors:
        try:
            min_age_days = metrics_config(load_scoring(root / SCORING_RELPATH)).min_age_days
        except ScoringConfigError as exc:
            print(f"analyse: {exc}", file=sys.stderr)
            return EXIT_ERROR

    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"analyse: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; using the default data directory")
        settings = None
    except SettingsError as exc:
        print(f"analyse: {exc}", file=sys.stderr)
        return EXIT_ERROR
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME

    if args.dry_run:
        # A migrated in-memory copy: the queries need 0004's columns and a dry run must
        # not migrate the real file.
        copy = read_copy(db_path)
        try:
            if args.summaries:
                _print_summary_plan(copy, *summary_paths(root), summary_opts)
            if args.competitors:
                _print_comparison_plan(copy, *competitor_paths(root), min_age_days)
        finally:
            copy.close()
        return EXIT_OK

    reason = claude_runner.check_available()
    if reason is not None:
        print(f"analyse: claude unavailable: {reason}", file=sys.stderr)
        if args.competitors:
            # The dashboard shows "analysis pending" from this row.
            prompt_path, schema_path = competitor_paths(root)
            conn = connect(db_path)
            try:
                with recorded_run(conn, "analyse_competitors") as run:
                    run.status = "error"
                    row_id = record_pending(
                        conn,
                        prompt_hash=claude_runner.file_hash(prompt_path),
                        schema_hash=claude_runner.file_hash(schema_path),
                        packet_path=None,
                        reason=reason,
                    )
            finally:
                conn.close()
            print(f"analyse --competitors: row {row_id} pending")
        return EXIT_CLAUDE_UNAVAILABLE

    assert settings is not None
    conn = connect(db_path)
    try:
        if args.summaries:
            assert summary_opts is not None
            code = _run_summaries(summary_opts, conn, root, settings)
            if code != EXIT_OK:
                if args.competitors:
                    print(
                        "analyse: skipping --competitors after the summaries failed",
                        file=sys.stderr,
                    )
                return code
        if args.competitors:
            return _run_competitors(conn, root, settings)
        return EXIT_OK
    finally:
        conn.close()


def _run_summaries(
    opts: _SummaryOptions, conn: sqlite3.Connection, root: Path, settings: Settings
) -> int:
    with recorded_run(conn, "analyse_summaries") as run:
        counts = summarise_videos(
            conn,
            repo_root=root,
            packets_dir=packets.packets_dir(settings.data_dir),
            limit=opts.limit,
            **opts.kwargs(),
            model=settings.claude.model,
            progress=print,
        )
        if counts.failure:
            run.status = "error"
    print(
        f"analyse --summaries: {counts.done} of {counts.candidates} candidates summarised "
        f"({counts.duration_s:.0f}s)"
    )
    if counts.failure:
        print(
            f"analyse: claude failed on {counts.failed_video_id}: {counts.failure}",
            file=sys.stderr,
        )
        return EXIT_CLAUDE_UNAVAILABLE if counts.done == 0 else EXIT_ERROR
    return EXIT_OK


def _run_competitors(conn: sqlite3.Connection, root: Path, settings: Settings) -> int:
    """The comparison call; exit 5 (row ``pending``) when ``claude`` fails."""
    with recorded_run(conn, "analyse_competitors") as run:
        outcome = analyse_competitors(
            conn,
            repo_root=root,
            packets_dir=packets.packets_dir(settings.data_dir),
            model=settings.claude.model,
        )
        if outcome.failure:
            run.status = "error"
    packet_name = outcome.packet_path.name if outcome.packet_path else "-"
    print(
        f"analyse --competitors: {outcome.channels} channels, {outcome.videos} summarised "
        f"videos, packet {packet_name}"
    )
    if outcome.failure:
        print(f"analyse: claude failed: {outcome.failure}", file=sys.stderr)
        print(f"analyse --competitors: row {outcome.row_id} pending")
        return EXIT_CLAUDE_UNAVAILABLE
    dropped = len(outcome.dropped_video_ids) + len(outcome.dropped_channel_ids)
    if dropped:
        print(
            "analyse --competitors: dropped ids not in the packet: "
            + ", ".join(outcome.dropped_video_ids + outcome.dropped_channel_ids)
        )
    usage = outcome.usage or {}
    tokens = f"{usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out"
    print(
        f"analyse --competitors: row {outcome.row_id} ok ({outcome.duration_s:.0f}s, "
        f"tokens {tokens}, {dropped} unknown id(s) dropped)"
    )
    return EXIT_OK


def _print_seed_plan(conn: sqlite3.Connection, root: Path) -> None:
    rpm = scout_propose.load_rpm_tiers(root / scout_propose.RPM_TIERS_RELPATH)
    seeds = scout_propose.load_seeds(root / scout_propose.SEEDS_RELPATH)
    known = scout_propose.step_ids(root)
    print("dry run: seeds that would be added (nothing is written)")
    would_add = 0
    for seed in seeds:
        if seed.topic_category not in rpm.categories:
            state = f"skip: unknown category {seed.topic_category}"
        elif any(s not in known for s in seed.suspected_manual_steps):
            state = "skip: unknown step id"
        elif repo.niche_exists(conn, seed.format, seed.topic):
            state = "already in the DB"
        else:
            state = "add"
            would_add += 1
        print(f"  {seed.format:8} {seed.topic}: {state}")
    print(f"seeds: {len(seeds)}; would add: {would_add}")


def _print_brainstorm_plan(conn: sqlite3.Connection, root: Path, count: int) -> None:
    prompt_path, schema_path = scout_propose.prompt_paths(root)
    packet = scout_propose.brainstorm_packet(conn, repo_root=root, count=count)
    size = len(json.dumps(packet, ensure_ascii=False).encode("utf-8"))
    print("dry run: the brainstorm that would run (claude is not called, nothing is written)")
    print(
        f"asking for {count} niches; {len(packet['existing_niches'])} already in the DB; "
        f"{len(packet['topic_categories'])} categories; {len(packet['production_steps'])} "
        f"steps; packet {size:,} bytes"
    )
    reason = claude_runner.check_available()
    print(f"claude: {'ok' if reason is None else reason}")
    print(
        f"prompt: {prompt_path.name} ({claude_runner.file_hash(prompt_path)}); "
        f"schema: {schema_path.name} ({claude_runner.file_hash(schema_path)})"
    )


def cmd_scout_propose(args: argparse.Namespace, _extras: list[str]) -> int:
    """``scout propose``: seeds (no API, no Claude) or one ``claude -p`` brainstorm."""
    root = find_repo_root()
    needed = [root / scout_propose.RPM_TIERS_RELPATH, root / STEPS_RELPATH]
    if args.from_seeds:
        needed.append(root / scout_propose.SEEDS_RELPATH)
    else:
        needed.extend(scout_propose.prompt_paths(root))
        needed.append(root / COVERAGE_RELPATH)
    for path in needed:
        if not path.is_file():
            print(f"scout propose: missing {path}", file=sys.stderr)
            return EXIT_ERROR

    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not (args.dry_run or args.from_seeds):
            print(f"scout propose: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; using the default data directory")
        settings = None
    except SettingsError as exc:
        print(f"scout propose: {exc}", file=sys.stderr)
        return EXIT_ERROR
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME

    try:
        if args.dry_run:
            copy = read_copy(db_path)
            try:
                if args.from_seeds:
                    _print_seed_plan(copy, root)
                else:
                    _print_brainstorm_plan(copy, root, args.count)
            finally:
                copy.close()
            return EXIT_OK

        if args.from_seeds:
            conn = connect(db_path)
            try:
                with recorded_run(conn, "scout_propose_seeds"):
                    result = scout_propose.propose_from_seeds(conn, repo_root=root)
            finally:
                conn.close()
            print(scout_propose.format_added(result))
            print(scout_propose.format_summary(result))
            return EXIT_OK

        reason = claude_runner.check_available()
        if reason is not None:
            print(f"scout propose: claude unavailable: {reason}", file=sys.stderr)
            return EXIT_CLAUDE_UNAVAILABLE
        assert settings is not None
        conn = connect(db_path)
        try:
            with recorded_run(conn, "scout_propose_llm") as run:
                result = scout_propose.propose_from_brainstorm(
                    conn,
                    repo_root=root,
                    packets_dir=packets.packets_dir(settings.data_dir),
                    count=args.count,
                    model=settings.claude.model,
                )
                if result.failure:
                    run.status = "error"
        finally:
            conn.close()
    except (scout_propose.ScoutConfigError, AuditError) as exc:
        print(f"scout propose: {exc}", file=sys.stderr)
        return EXIT_ERROR

    packet_name = result.packet_path.name if result.packet_path else "-"
    if result.failure:
        print(f"scout propose: claude failed: {result.failure}", file=sys.stderr)
        print(f"scout propose: nothing added (packet {packet_name})")
        return EXIT_CLAUDE_UNAVAILABLE
    print(scout_propose.format_added(result))
    if result.unknown_categories:
        print("unknown categories skipped: " + ", ".join(sorted(set(result.unknown_categories))))
    if result.unknown_steps:
        print("unknown step ids skipped: " + ", ".join(sorted(set(result.unknown_steps))))
    usage = result.usage or {}
    tokens = f"{usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out"
    print(
        f"{scout_propose.format_summary(result)} ({result.duration_s:.0f}s, tokens {tokens}, "
        f"packet {packet_name}, prompt {result.prompt_hash})"
    )
    return EXIT_OK


def _validate_targets(
    conn: sqlite3.Connection, args: argparse.Namespace
) -> list[sqlite3.Row] | str:
    """The niches ``scout validate`` should run on, or an error message."""
    if args.all_proposed:
        return repo.proposed_niches(conn)
    row = repo.get_niche(conn, args.niche)
    if row is None:
        return f"no niche {args.niche}"
    if row["status"] not in scout_validate.VALIDATABLE:
        return (
            f"niche {args.niche} is {row['status']!r}; only proposed or validated niches are"
            " (re)validated, so a tracking or shelved decision is never undone"
        )
    return [row]


def _print_validate_plan(
    niches: Sequence[sqlite3.Row], cfg: scout_validate.ValidationConfig, ledger: Ledger
) -> None:
    print("dry run: planned YouTube Data API calls (nothing is sent, nothing is written)")
    left = ledger.remaining_today()
    if ledger.run_cap is not None:
        left = min(left, ledger.run_cap)
    total = 0
    fits = 0
    for niche in niches:
        specs = scout_validate.planned_searches(niche)
        units = scout_validate.worst_case_units(cfg, len(specs)) if specs else 0
        label = niche["label"] or niche["topic"]
        print(f"niche {niche['id']} [{niche['format']}] {label}: estimate <= {units} units")
        for spec in specs:
            print(
                f"  search.list q={spec.query!r} order={spec.order}"
                f" videoDuration={spec.video_duration} - 100 units"
            )
        if not specs:
            print("  skipped: no search queries")
        elif total + units <= left:
            fits += 1
        total += units
    per_channel = (
        f"up to {cfg.max_channels} channels: "
        f"{math.ceil(cfg.max_channels / 50)} channels.list + "
        f"{cfg.max_channels} playlistItems.list + {cfg.max_channels} videos.list"
    )
    print(f"per niche, after the searches: {per_channel}")
    print(
        f"planned: {len(niches)} niche(s), at most {total} units; {fits} fit in the"
        f" {left} units left under the caps"
    )


def _print_validate_skips(niche_ids: Sequence[int], *, dry_run: bool) -> None:
    verb = "would skip" if dry_run else "skipping"
    if niche_ids:
        ids = ", ".join(str(i) for i in niche_ids)
        print(f"resume: {verb} niche(s) validated in the last 10 days: {ids}")
    else:
        print("resume: no niche here was validated in the last 10 days")


def cmd_scout_validate(args: argparse.Namespace, _extras: list[str]) -> int:
    """``scout validate``: searches → channels → one uploads page + videos per channel.

    Exit 3 when a quota cap stops the run; finished niches stay validated.
    """
    if not _require_quota_flag(args, "scout validate"):
        return EXIT_ERROR
    root = find_repo_root()
    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"scout validate: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; using the default data directory")
        settings = None
    except SettingsError as exc:
        print(f"scout validate: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        scoring = load_scoring(root / SCORING_RELPATH)
        shorts_max = shorts_max_seconds(scoring)
        cfg = validation_config(scoring)
    except ScoringConfigError as exc:
        print(f"scout validate: {exc}", file=sys.stderr)
        return EXIT_ERROR
    daily_cap = settings.quota.daily_cap if settings else DEFAULT_QUOTA_DAILY_CAP
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME

    if args.dry_run:
        copy = read_copy(db_path)
        try:
            targets = _validate_targets(copy, args)
            ledger = Ledger(copy, daily_cap, run_cap=args.max_units, dry_run=True)
            if isinstance(targets, str):
                print(f"scout validate: {targets}", file=sys.stderr)
                return EXIT_ERROR
            if args.resume:
                now = utc_now()
                done = [
                    n
                    for n in targets
                    if done_keys(copy, KIND_VALIDATE, validate_run_id(n["id"]), now)
                ]
                _print_validate_skips([int(n["id"]) for n in done], dry_run=True)
                targets = [n for n in targets if n not in done]
            _print_validate_plan(targets, cfg, ledger)
        finally:
            copy.close()
        return EXIT_OK

    try:
        transport = make_transport(settings, False)
    except ValueError as exc:
        print(f"scout validate: {exc}", file=sys.stderr)
        return EXIT_ERROR
    conn = connect(db_path)
    try:
        targets = _validate_targets(conn, args)
        if isinstance(targets, str):
            print(f"scout validate: {targets}", file=sys.stderr)
            return EXIT_ERROR
        if not targets:
            print("scout validate: no proposed niches; run `scout propose` first")
            return EXIT_OK
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        with recorded_run(conn, "scout_validate") as run:
            result = scout_validate.validate_niches(
                DataApi(ledger, transport),
                conn,
                targets,
                cfg=cfg,
                shorts_max_seconds=shorts_max,
                now=utc_now(),
                resume=args.resume,
            )
            if result.stopped is not None:
                run.status = "quota_exhausted"
        if args.resume:
            _print_validate_skips(result.skipped, dry_run=False)
        for report in result.reports:
            print(scout_validate.format_report(report))
        print(
            f"scout validate: {len(result.validated)} of {len(targets)} niche(s) validated;"
            f" units this run {ledger.run_used}, units today {ledger.used_today()}"
        )
        if result.stopped is not None:
            if result.not_started is not None:
                need = scout_validate.worst_case_units(cfg)
                print(
                    f"stopped before niche {result.not_started.niche_id}: it needs up to"
                    f" {need} units and fewer are left ({result.stopped})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"stopped early; finished niches are committed: {result.stopped}",
                    file=sys.stderr,
                )
            _print_resume_hint(args)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def _print_snowball_plan(queries: Sequence[scout_snowball.PlannedQuery], ledger: Ledger) -> None:
    print("dry run: planned YouTube Data API calls (nothing is sent, nothing is written)")
    for planned in queries:
        print(
            f"  search.list q={planned.query!r} order=viewCount"
            f" (from video {planned.source_video} of {planned.source_channel}) - 100 units"
        )
    n = len(queries)
    units = scout_snowball.worst_case_units(n)
    print(
        f"then channels.list on channels with >= {scout_snowball.MIN_HITS} hits and"
        " videos.list on the clusters' hit videos (1 unit per 50 ids)"
    )
    left = scout_snowball.headroom(ledger)
    fits = scout_snowball.searches_within(left, n)
    print(
        f"planned: {n} search(es), estimate <= {units} units; {fits} fit in the"
        f" {left} units left under the caps"
    )


def cmd_scout_snowball(args: argparse.Namespace, _extras: list[str]) -> int:
    """``scout snowball``: known channels' top titles → searches → clusters → proposals.

    Exit 3 when a quota cap stops the run; nothing is written then.
    """
    if args.max_searches > scout_snowball.MAX_SEARCHES_CAP:
        print(
            f"scout snowball: --max-searches {args.max_searches} is above"
            f" {scout_snowball.MAX_SEARCHES_CAP} ({scout_snowball.MAX_SEARCHES_CAP * 100}"
            " search units); refusing",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if not _require_quota_flag(args, "scout snowball"):
        return EXIT_ERROR
    root = find_repo_root()
    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"scout snowball: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; using the default data directory")
        settings = None
    except SettingsError as exc:
        print(f"scout snowball: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        scoring = load_scoring(root / SCORING_RELPATH)
        shorts_max = shorts_max_seconds(scoring)
        cfg = validation_config(scoring)
    except ScoringConfigError as exc:
        print(f"scout snowball: {exc}", file=sys.stderr)
        return EXIT_ERROR
    daily_cap = settings.quota.daily_cap if settings else DEFAULT_QUOTA_DAILY_CAP
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME

    if args.dry_run:
        copy = read_copy(db_path)
        try:
            queries = scout_snowball.plan_queries(copy, args.max_searches)
            ledger = Ledger(copy, daily_cap, run_cap=args.max_units, dry_run=True)
            _print_snowball_plan(queries, ledger)
        finally:
            copy.close()
        return EXIT_OK

    try:
        transport = make_transport(settings, False)
    except ValueError as exc:
        print(f"scout snowball: {exc}", file=sys.stderr)
        return EXIT_ERROR
    conn = connect(db_path)
    try:
        queries = scout_snowball.plan_queries(conn, args.max_searches)
        if not queries:
            print(
                "scout snowball: no new queries (approve competitors or track a niche first,"
                " or every query is already used by a niche)"
            )
            return EXIT_OK
        ledger = Ledger(conn, daily_cap, run_cap=args.max_units)
        fits = scout_snowball.searches_within(scout_snowball.headroom(ledger), len(queries))
        if fits == 0:
            need = scout_snowball.worst_case_units(1)
            print(
                f"scout snowball: one search needs up to {need} units and fewer are left"
                " under --max-units or today's cap",
                file=sys.stderr,
            )
            return EXIT_QUOTA_EXHAUSTED
        if fits < len(queries):
            print(f"note: the unit caps allow {fits} of {len(queries)} searches")
            queries = queries[:fits]
        with recorded_run(conn, "scout_snowball") as run:
            result = scout_snowball.snowball(
                DataApi(ledger, transport),
                conn,
                queries,
                lookback_days=cfg.lookback_days,
                language=cfg.language,
                shorts_max_seconds=shorts_max,
                now=utc_now(),
            )
            if result.stopped is not None:
                run.status = "quota_exhausted"
        for niche_id, proposal in result.added:
            print(scout_snowball.format_proposal(niche_id, proposal))
        for proposal, reason in result.skipped:
            print(f"skipped: {scout_snowball.format_proposal(None, proposal)} ({reason})")
        print(
            f"scout snowball: {result.searches} searches, {result.candidate_channels} new"
            f" channels, {result.kept_channels} with >= {scout_snowball.MIN_HITS} hits,"
            f" {result.clusters} cluster(s), {len(result.added)} niche(s) proposed;"
            f" units this run {ledger.run_used}, units today {ledger.used_today()}"
        )
        if result.stopped is not None:
            print(f"stopped early; nothing was written: {result.stopped}", file=sys.stderr)
            return EXIT_QUOTA_EXHAUSTED
        return EXIT_OK
    finally:
        conn.close()


def cmd_scout_tag(args: argparse.Namespace, _extras: list[str]) -> int:
    """``scout tag``: one ``claude -p`` call per 10 validated niches lacking current tags."""
    root = find_repo_root()
    prompt_path, schema_path = scout_tag.prompt_paths(root)
    for path in (prompt_path, schema_path, root / STEPS_RELPATH, root / SCORING_RELPATH):
        if not path.is_file():
            print(f"scout tag: missing {path}", file=sys.stderr)
            return EXIT_ERROR
    settings: Settings | None
    try:
        settings = load(repo_root=root)
    except SettingsMissing as exc:
        if not args.dry_run:
            print(f"scout tag: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"note: {exc.path} not found; using the default data directory")
        settings = None
    except SettingsError as exc:
        print(f"scout tag: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        shorts_max = shorts_max_seconds(load_scoring(root / SCORING_RELPATH))
    except ScoringConfigError as exc:
        print(f"scout tag: {exc}", file=sys.stderr)
        return EXIT_ERROR
    db_path = default_db_path(settings) if settings else root / DEFAULT_DATA_DIR / DB_FILENAME
    prompt_hash = claude_runner.file_hash(prompt_path)

    if args.dry_run:
        copy = read_copy(db_path)
        try:
            targets = repo.niches_to_tag(copy, prompt_hash, args.limit)
            print("dry run: planned claude -p calls (nothing is called, nothing is written)")
            for n, batch in enumerate(scout_tag.batches(targets), 1):
                print(f"call {n}: niches " + ", ".join(str(r["id"]) for r in batch))
                for row in batch:
                    titles = scout_tag.sample_titles(copy, row, shorts_max)
                    print(
                        f"  {row['id']} [{row['format']}] {row['label'] or row['topic']}"
                        f" ({len(titles)} sample title(s))"
                    )
        finally:
            copy.close()
        calls = math.ceil(len(targets) / scout_tag.BATCH_SIZE)
        print(
            f"planned: {len(targets)} niche(s) in {calls} call(s); prompt {prompt_path.name}"
            f" ({prompt_hash}), schema {schema_path.name}"
            f" ({claude_runner.file_hash(schema_path)})"
        )
        return EXIT_OK

    assert settings is not None
    conn = connect(db_path)
    try:
        if not repo.niches_to_tag(conn, prompt_hash, 1):
            print("scout tag: nothing to tag (no validated niche lacks current tags)")
            return EXIT_OK
        reason = claude_runner.check_available()
        if reason is not None:
            print(f"scout tag: claude unavailable: {reason}", file=sys.stderr)
            return EXIT_CLAUDE_UNAVAILABLE
        with recorded_run(conn, "scout_tag") as run:
            result = scout_tag.tag_niches(
                conn,
                repo_root=root,
                packets_dir=packets.packets_dir(settings.data_dir),
                shorts_max_seconds=shorts_max,
                limit=args.limit,
                model=settings.claude.model,
            )
            if result.failure:
                run.status = "error"
    finally:
        conn.close()
    print(
        f"scout tag: {len(result.tagged)} of {result.candidates} niche(s) tagged in"
        f" {result.calls} call(s) ({result.duration_s:.0f}s, prompt {result.prompt_hash})"
    )
    if result.missing:
        print("not in Claude's answer, left untagged: " + ", ".join(map(str, result.missing)))
    if result.failure:
        print(f"scout tag: claude failed: {result.failure}", file=sys.stderr)
        return EXIT_CLAUDE_UNAVAILABLE
    return EXIT_OK


def _make_stub(name: str, issue: str):
    def run(_args: argparse.Namespace, _extras: list[str]) -> int:
        print(f"ytscout {name}: not implemented yet (issue {issue})", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    return run


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def cmd_doctor(args: argparse.Namespace, _extras: list[str]) -> int:
    """Print the dependency table; exit 1 when a required check fails (030)."""
    report = doctor_mod.run_checks(offline=args.offline)
    print(doctor_mod.format_table(report, ascii_only=not doctor_mod.can_print_marks(sys.stdout)))
    return EXIT_OK if report.ok else EXIT_ERROR


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
    # Windows consoles default to cp1252; a channel title with an emoji must not crash a
    # command after its work is committed (009). Replace what cannot be encoded.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    # Stubs accept any flags a later issue will define (`collect --own --max-units 500`)
    # and still exit 2, so run_weekly.ps1 can skip them. Real commands reject unknown flags.
    args, extras = parser.parse_known_args(argv)
    if extras and not getattr(args, "stub", False):
        parser.error(f"unrecognized arguments: {' '.join(extras)}")
    # The exact command, for the `resume with:` line a quota stop prints (032).
    args.argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args, extras))
    except tls.TlsError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
