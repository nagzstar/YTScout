"""Command-line entry point: ``python -m ytscout <command>``.

Exit codes (DESIGN.md §9.2): 0 ok · 1 error · 2 not implemented yet · 3 quota exhausted ·
4 no OAuth token · 5 ``claude`` unavailable. Stubs exit 2 so ``scripts/run_weekly.ps1``
can skip steps that no issue has delivered yet.
"""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

from ytscout import __version__
from ytscout.audit import COVERAGE_RELPATH, STEPS_RELPATH, AuditError, format_table, run_audit
from ytscout.settings import Settings, SettingsError, find_repo_root, load

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_QUOTA_EXHAUSTED = 3
EXIT_NO_OAUTH_TOKEN = 4
EXIT_CLAUDE_UNAVAILABLE = 5

# command -> (help text, issue that delivers it). Order is the order shown in --help.
STUBS: dict[str, tuple[str, str]] = {
    "auth": ("OAuth consent for the Analytics API", "011"),
    "collect": ("pull own/competitor/analytics/transcript/niche data into SQLite", "004"),
    "discover": ("find candidate competitors", "006"),
    "packet": ("write one analysis packet for Claude", "016"),
    "analyse": ("run Claude analyses over packets", "017"),
    "score": ("compute scores from the DB", "024"),
    "scout": ("the niche pipeline: propose / validate / tag / snowball / sensitivity", "021"),
    "dashboard": ("build dashboard/index.html", "007"),
    "serve": ("localhost review server for approve/reject", "008"),
}

COMMANDS: tuple[str, ...] = ("doctor", *STUBS, "audit")


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
