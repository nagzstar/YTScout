"""CLI: help lists every command, stubs exit 2 (doctor itself: test_doctor.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ytscout.cli import (
    COMMANDS,
    EXIT_NOT_IMPLEMENTED,
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


def test_command_table_is_exactly_the_eleven() -> None:
    assert set(COMMANDS) == EXPECTED
    assert len(COMMANDS) == 11


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


def test_no_top_level_stubs_are_left() -> None:
    """Every top-level command is real since 021; only scout subcommands still stub."""
    assert STUBS == {}
    assert set(SCOUT_STUBS) == {"sensitivity"}


@pytest.mark.parametrize("name", sorted(SCOUT_STUBS))
def test_each_scout_stub_exits_2_with_not_implemented_line(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scout", name])
    assert code == EXIT_NOT_IMPLEMENTED
    err = capsys.readouterr().err
    assert err.startswith(f"ytscout scout {name}: not implemented yet (issue ")


def test_scout_stub_swallows_future_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """A future flag on a stub is still a 2, not a usage error."""
    assert main(["scout", "sensitivity", "--max-units", "500"]) == EXIT_NOT_IMPLEMENTED
    assert "not implemented" in capsys.readouterr().err


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
