"""CLI: help lists every command, stubs exit 2 (doctor itself: test_doctor.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ytscout.cli import (
    COMMANDS,
    EXIT_OK,
    SCOUT_STUBS,
    STUBS,
    main,
)

EXPECTED = {
    "doctor",
    "auth",
    "collect",
    "discover",
    "packet",
    "analyse",
    "score",
    "scout",
    "dashboard",
    "serve",
    "decide",
    "audit",
}


def run_cli(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ytscout", *argv],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def test_command_table_is_exactly_the_twelve() -> None:
    assert set(COMMANDS) == EXPECTED
    assert len(COMMANDS) == 12


def test_help_exits_zero_and_lists_all_commands() -> None:
    result = run_cli("--help")
    assert result.returncode == EXIT_OK, result.stderr
    for name in EXPECTED:
        assert name in result.stdout


def test_no_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    assert "usage" in capsys.readouterr().err


def test_no_stubs_are_left() -> None:
    """Every top-level command is real since 021 and every scout subcommand since 028."""
    assert STUBS == {}
    assert SCOUT_STUBS == {}


def test_scout_sensitivity_rejects_quota_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """The stub swallowed --max-units; the real command spends no units and refuses it."""
    with pytest.raises(SystemExit) as excinfo:
        main(["scout", "sensitivity", "--max-units", "500"])
    assert excinfo.value.code == 2
    assert "unrecognized" in capsys.readouterr().err


def test_scout_without_a_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["scout"])
    assert excinfo.value.code == 2
    assert "usage" in capsys.readouterr().err


def test_doctor_rejects_unknown_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["doctor", "--bogus"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_collect_own_without_max_units_refuses_via_subprocess(tmp_path: Path) -> None:
    result = run_cli("collect", "--own", cwd=tmp_path)
    assert result.returncode != EXIT_OK
    assert "--max-units" in result.stderr


def test_collect_accepts_resume() -> None:
    """run_weekly.ps1 -Resume passes --resume to every collector, including the ones that
    ignore it (--analytics, --transcripts)."""
    from ytscout.cli import build_parser

    args = build_parser().parse_args(["collect", "--own", "--resume", "--dry-run"])
    assert args.resume is True
