"""``ytscout doctor``: every dependency checked, nothing printed that shouldn't be.

Each check yields one row: a status (✓ pass, ✗ fail, – skipped, · info), whether it is
required, and a one-line reason. ``doctor`` exits 1 when any **required** check fails.

The three network probes (Data API, OAuth token, ``claude`` login) are skipped with
``offline=True``. Online, the Data API probe is one ``channels.list`` for the own channel
through the ledger (1 unit) and the login probe is one real ``claude -p`` call.

Nothing secret is printed: the API key is reported as set or not, the own channel id is
never shown, and every reason is scrubbed of the API key before it leaves this module (a
``googleapiclient`` error message carries the request URL, key included).

This module does not import ``ytscout.collect``: when a collector is broken, doctor must
still run and say so.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ytscout import claude_runner
from ytscout.audit import COVERAGE_RELPATH
from ytscout.settings import Settings, SettingsError, find_repo_root, load
from ytscout.store import connect, default_db_path
from ytscout.store.db import applied_versions, migration_files
from ytscout.youtube import tls

if TYPE_CHECKING:
    from ytscout.youtube import Transport

PASS = "✓"
FAIL = "✗"
SKIP = "–"
INFO = "·"

Required = Literal["yes", "no", "info"]

MIN_PYTHON = (3, 12)
LOGIN_TIMEOUT_S = 180
LOGIN_PROMPT = "reply with ok"


@dataclass
class Check:
    name: str
    required: Required
    status: str
    reason: str

    @property
    def failed_required(self) -> bool:
        return self.required == "yes" and self.status == FAIL


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)

    def add(self, name: str, required: Required, status: str, reason: str) -> Check:
        check = Check(name, required, status, self.scrub(reason))
        self.checks.append(check)
        return check

    def scrub(self, text: str) -> str:
        text = " ".join(str(text).split())  # one line
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    @property
    def ok(self) -> bool:
        return not any(c.failed_required for c in self.checks)


# --- probes: one per network or process dependency; tests replace them ---------------------

Outcome = tuple[str, str]  # (status, reason)


def probe_data_api(
    settings: Settings,
    conn: sqlite3.Connection,
    ca_certs: Path | None,
    transport: Transport | None = None,
) -> Outcome:
    """``channels.list`` for the own channel: 1 unit, charged through the ledger."""
    from googleapiclient.errors import HttpError

    from ytscout.youtube import DataApi, GoogleTransport, Ledger, QuotaExhausted

    ledger = Ledger(conn, settings.quota.daily_cap, run_cap=1)
    if transport is None:
        transport = GoogleTransport(settings.api_key, ca_certs=ca_certs)
    api = DataApi(ledger, transport)
    try:
        body = api.channels([settings.own_channel_id], part="id")
    except QuotaExhausted as exc:
        return FAIL, str(exc)
    except HttpError as exc:
        return FAIL, f"HTTP {exc.resp.status}: {exc.reason}"
    if not body.get("items"):
        return FAIL, "reachable, but the own channel id was not found (check own_channel_id)"
    return PASS, f"channels.list ok ({ledger.run_used} unit charged)"


def probe_token(settings: Settings, ca_certs: Path | None) -> Outcome:
    """Load the token, refresh it, and check its scopes. Scope names only, never values."""
    from ytscout.youtube.oauth import TokenError, check_scopes, load_credentials, refresh_token

    try:
        credentials = load_credentials(settings.token_path, refresh=False)
    except TokenError as exc:
        return FAIL, str(exc)
    if credentials is None:
        return FAIL, "no token file (run `ytscout auth`, an Active step)"
    if credentials.refresh_token:
        try:
            refresh_token(credentials, settings.token_path, ca_certs=ca_certs)
        except TokenError as exc:
            return FAIL, str(exc)
        refreshed = "refreshed"
    elif credentials.expired:
        return FAIL, "expired and has no refresh token (run `ytscout auth`)"
    else:
        refreshed = "valid, no refresh token to test"
    problems = check_scopes(credentials)
    if problems:
        return FAIL, f"{refreshed}; " + "; ".join(problems)
    return PASS, f"{refreshed}; scopes read-only, monetary present"


def claude_path() -> str | None:
    return claude_runner.executable()


def claude_version() -> tuple[int, ...]:
    return claude_runner.version()


def probe_claude_login(settings: Settings) -> Outcome:
    """One real ``claude -p`` call on the subscription; ok when it returns a non-error result."""
    exe = claude_path()
    if exe is None:
        return FAIL, "`claude` is not on PATH"
    argv = [
        exe,
        "-p",
        LOGIN_PROMPT,
        "--output-format",
        "json",
        "--allowedTools",
        "Read",
        "--permission-prompts",
        "none",
    ]
    if settings.claude.model:
        argv += ["--model", settings.claude.model]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LOGIN_TIMEOUT_S,
            cwd=settings.repo_root,
            env=claude_runner.child_env(),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return FAIL, f"`claude -p` timed out after {LOGIN_TIMEOUT_S}s"
    except OSError as exc:
        return FAIL, f"could not start claude: {exc}"
    if proc.returncode != 0:
        return FAIL, f"`claude -p` exited {proc.returncode} (not logged in? run `claude` once)"
    try:
        result = claude_runner._parse_stdout(proc.stdout)
    except claude_runner.ClaudeUnavailable as exc:
        return FAIL, exc.reason
    if result.get("is_error"):
        return FAIL, f"`claude -p` reported an error ({result.get('subtype', '?')})"
    return PASS, "`claude -p` answered on the subscription"


# --- the checks ------------------------------------------------------------------------------


def _guard(report: Report, name: str, required: Required, fn: Callable[[], Outcome]) -> str:
    """Run one check; an unexpected exception is a failure with its type, not a crash."""
    try:
        status, reason = fn()
    except Exception as exc:  # noqa: BLE001  (doctor reports, it does not raise)
        status, reason = FAIL, f"{type(exc).__name__}: {exc}"
    report.add(name, required, status, reason)
    return status


def _versions_on_disk(db_path: Path) -> set[int]:
    """Migrations applied to the file before doctor opened it (read-only; empty if none)."""
    if not db_path.is_file():
        return set()
    raw = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return applied_versions(raw)
    except sqlite3.OperationalError:
        return set()  # no schema_migrations table yet
    finally:
        raw.close()


def run_checks(*, offline: bool = False, start: Path | None = None) -> Report:
    """Every check in the order the table prints them."""
    report = Report()
    offline_note = "skipped (--offline)"

    version = ".".join(str(p) for p in sys.version_info[:3])
    report.add(
        "Python >= 3.12",
        "yes",
        PASS if sys.version_info[:2] >= MIN_PYTHON else FAIL,
        version,
    )

    settings: Settings | None = None
    try:
        settings = load(repo_root=find_repo_root(start))
    except SettingsError as exc:
        report.add("settings.yaml and .env load", "yes", FAIL, str(exc).replace("\n", "; "))
    else:
        report.secrets.append(settings.api_key or "")
        report.add(
            "settings.yaml and .env load",
            "yes",
            PASS,
            f"{settings.settings_path.relative_to(settings.repo_root)}",
        )

    def needs(name: str, required: Required, what: str) -> None:
        report.add(name, required, SKIP, f"needs {what}")

    conn: sqlite3.Connection | None = None
    ca_certs: Path | None = None
    if settings is None:
        for name, req in (
            ("data_dir writable", "yes"),
            ("DB opens, migrations current", "yes"),
            ("YT_API_KEY set", "yes"),
            ("TLS CA bundle", "no"),
        ):
            needs(name, req, "settings")  # type: ignore[arg-type]
    else:

        def writable() -> Outcome:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=settings.data_dir, prefix=".doctor-"):
                pass
            return PASS, str(settings.data_dir)

        _guard(report, "data_dir writable", "yes", writable)

        def database() -> Outcome:
            nonlocal conn
            db_path = default_db_path(settings)
            before = _versions_on_disk(db_path)
            conn = connect(db_path)
            have = applied_versions(conn)
            want = {v for v, _ in migration_files()}
            if want - have:
                return FAIL, f"migrations missing: {sorted(want - have)}"
            latest = max(want) if want else 0
            note = "current" if before >= want else f"migrated now from {len(before)} applied"
            return PASS, f"schema v{latest}, {note}"

        _guard(report, "DB opens, migrations current", "yes", database)

        report.add(
            "YT_API_KEY set",
            "yes",
            PASS if settings.has_api_key else FAIL,
            "set (value not shown)" if settings.has_api_key else "not set in .env",
        )

        def bundle() -> Outcome:
            nonlocal ca_certs
            found = tls.ca_bundle(settings.data_dir)
            ca_certs = found.path
            return PASS, found.describe()

        _guard(report, "TLS CA bundle", "no", bundle)

    # Data API
    name = "Data API reachable"
    if offline:
        report.add(name, "yes", SKIP, offline_note)
    elif settings is None or conn is None:
        needs(name, "yes", "settings and the DB")
    elif not settings.has_api_key:
        needs(name, "yes", "YT_API_KEY")
    else:
        s, c, ca = settings, conn, ca_certs
        _guard(report, name, "yes", lambda: probe_data_api(s, c, ca))

    if settings is None:
        needs("client secret file present", "no", "settings")
        needs("token present, refreshes, scopes right", "no", "settings")
    else:
        present = settings.client_secret_path.is_file()
        report.add(
            "client secret file present",
            "no",
            PASS if present else FAIL,
            f"{_rel(settings, settings.client_secret_path)}: {'present' if present else 'missing'}",
        )
        name = "token present, refreshes, scopes right"
        if offline:
            report.add(name, "no", SKIP, offline_note)
        else:
            s, ca = settings, ca_certs
            _guard(report, name, "no", lambda: probe_token(s, ca))

    # claude
    exe = claude_path()
    report.add(
        "claude on PATH",
        "yes",
        PASS if exe else FAIL,
        exe or "not found (install Claude Code)",
    )
    minimum = claude_runner.version_string(claude_runner.MIN_VERSION)
    name = f"claude version >= {minimum}"
    if exe is None:
        needs(name, "yes", "claude on PATH")
    else:

        def claude_ver() -> Outcome:
            found = claude_version()
            ok = found >= claude_runner.MIN_VERSION
            return (PASS if ok else FAIL), claude_runner.version_string(found)

        _guard(report, name, "yes", claude_ver)
    name = "claude logged in"
    if offline:
        report.add(name, "yes", SKIP, offline_note)
    elif exe is None:
        needs(name, "yes", "claude on PATH")
    elif settings is None:
        needs(name, "yes", "settings")
    else:
        s = settings
        _guard(report, name, "yes", lambda: probe_claude_login(s))

    # pipeline repo and audit output
    root = settings.repo_root if settings else find_repo_root(start)
    if settings is None:
        needs("pipeline_repo_path exists", "no", "settings")
    else:
        is_dir = settings.pipeline_repo_path.is_dir()
        report.add(
            "pipeline_repo_path exists",
            "no",
            PASS if is_dir else FAIL,
            f"{settings.pipeline_repo_path}",
        )
    coverage = root / COVERAGE_RELPATH
    report.add(
        "pipeline_coverage.yaml present",
        "no",
        PASS if coverage.is_file() else FAIL,
        str(COVERAGE_RELPATH) if coverage.is_file() else "missing (run the M3 pipeline audit)",
    )

    # history from the DB
    if conn is None:
        needs("last weekly run", "no", "the DB")
        needs("quota today", "info", "the DB")
        needs("money model calibration", "info", "the DB")
    else:
        c = conn
        _guard(report, "last weekly run", "no", lambda: _last_run(c))
        if settings is not None:
            s = settings
            _guard(report, "quota today", "info", lambda: _quota_today(s, c))
        _guard(report, "money model calibration", "info", lambda: _calibration(root, c))
        conn.close()
    return report


def _rel(settings: Settings, path: Path) -> str:
    try:
        return str(path.relative_to(settings.repo_root))
    except ValueError:
        return str(path)


def _last_run(conn: sqlite3.Connection) -> Outcome:
    row = conn.execute(
        "SELECT kind, started_at, finished_at, status FROM runs ORDER BY started_at DESC, id DESC"
        " LIMIT 1"
    ).fetchone()
    if row is None:
        return SKIP, "no runs recorded"
    status = row["status"] or ("running or crashed" if row["finished_at"] is None else "?")
    good = row["status"] == "ok"
    return (PASS if good else FAIL), f"{row['kind']} started {row['started_at']}: {status}"


def _quota_today(settings: Settings, conn: sqlite3.Connection) -> Outcome:
    from ytscout.youtube import Ledger

    ledger = Ledger(conn, settings.quota.daily_cap)
    used = ledger.used_today()
    return INFO, (
        f"{used} used, {ledger.remaining_today()} remaining of {ledger.daily_cap} "
        f"(Pacific day {ledger.today()})"
    )


def _calibration(root: Path, conn: sqlite3.Connection) -> Outcome:
    """Whether own Shorts Analytics carry an RPM to calibrate §6.3 with. Presence only: the
    RPM itself is own-channel money and is never printed (050)."""
    # Imported here: scout.score pulls in the collectors, which plain doctor must not load.
    from ytscout.scoring import money
    from ytscout.scoring.config import SCORING_RELPATH, load_scoring, shorts_max_seconds
    from ytscout.scout.score import own_rpm_usd
    from ytscout.store.db import utc_now

    path = root / SCORING_RELPATH
    if not path.is_file():
        return SKIP, f"needs {SCORING_RELPATH}"
    shorts_max = shorts_max_seconds(load_scoring(path))
    own = own_rpm_usd(conn, now=utc_now(), shorts_max_seconds=shorts_max)
    if own is None or own <= 0:
        return INFO, money.UNCALIBRATED_LABEL
    return INFO, "calibrated from own Shorts RPM (value not shown)"


# --- output ----------------------------------------------------------------------------------


# A console that cannot encode the marks (cp1252 when piped) would print "?" for both
# pass and fail; these stand in there.
ASCII_MARKS = {PASS: "ok", FAIL: "FAIL", SKIP: "-", INFO: "i"}


def can_print_marks(stream: object) -> bool:
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(ASCII_MARKS).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def format_table(report: Report, *, ascii_only: bool = False) -> str:
    header = ("", "check", "required", "detail")
    rows = [
        (ASCII_MARKS[c.status] if ascii_only else c.status, c.name, c.required, c.reason)
        for c in report.checks
    ]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(3)]
    lines = []
    for row in [header, *rows]:
        cells = [row[i].ljust(widths[i]) for i in range(3)] + [row[3]]
        lines.append("  ".join(cells).rstrip())
    failed = [c.name for c in report.checks if c.failed_required]
    lines.append("")
    lines.append("doctor: ok" if not failed else f"doctor: {len(failed)} required check(s) failed")
    return "\n".join(lines)
