"""CLI: help lists every command, stubs exit 2, doctor exits 1/0 on missing/present settings."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ytscout.cli import COMMANDS, EXIT_ERROR, EXIT_NOT_IMPLEMENTED, EXIT_OK, STUBS, main

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


@pytest.mark.parametrize("name", sorted(STUBS))
def test_each_stub_exits_2_with_not_implemented_line(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([name])
    assert code == EXIT_NOT_IMPLEMENTED
    err = capsys.readouterr().err
    assert err.startswith(f"ytscout {name}: not implemented yet (issue ")


def test_stub_swallows_future_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """run_weekly.ps1 will call e.g. `collect --own --max-units 500`; that must still be a 2."""
    assert main(["collect", "--own", "--max-units", "500"]) == EXIT_NOT_IMPLEMENTED
    assert "not implemented" in capsys.readouterr().err


def test_doctor_rejects_unknown_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["doctor", "--bogus"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_collect_via_subprocess_exits_2() -> None:
    result = run_cli("collect")
    assert result.returncode == EXIT_NOT_IMPLEMENTED
    assert "ytscout collect: not implemented yet" in result.stderr


def test_doctor_exits_1_without_settings(tmp_path: Path) -> None:
    result = run_cli("doctor", cwd=tmp_path)
    assert result.returncode == EXIT_ERROR, result.stdout + result.stderr
    assert "settings: MISSING" in result.stdout
    assert "settings.example.yaml" in result.stdout


def test_doctor_exits_0_with_minimal_settings(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(
        "own_channel_id: UCtest123\n", encoding="utf-8"
    )
    result = run_cli("doctor", cwd=tmp_path)
    assert result.returncode == EXIT_OK, result.stdout + result.stderr
    out = result.stdout
    assert "settings: ok" in out
    assert "YT_API_KEY: no" in out
    assert "client_secret.json: no" in out
    assert "token.json: no" in out
    assert "UCtest123" not in out  # doctor reports presence, never values


def test_doctor_never_prints_the_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(
        "own_channel_id: UCtest123\n", encoding="utf-8"
    )
    monkeypatch.setenv("YT_API_KEY", "fake-key-value-for-test")
    result = run_cli("doctor", cwd=tmp_path)
    assert result.returncode == EXIT_OK
    assert "YT_API_KEY: yes" in result.stdout
    assert "fake-key-value-for-test" not in result.stdout + result.stderr
