"""scripts/run_weekly.ps1 -DryRun: exit 0, the steps in order, --max-units on every collector."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="powershell not on PATH")

EXPECTED_STEPS = [
    "collect --own",
    "collect --competitors",
    "collect --analytics",
    "collect --transcripts",
    "collect --niches",
    "analyse --summaries",
    "analyse --competitors",
    "score",
    "dashboard",
]


def _dry_run(*extra: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO / "scripts" / "run_weekly.ps1"),
            "-DryRun",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
    )


def _commands(stdout: str) -> list[str]:
    lines = [line.strip() for line in stdout.splitlines() if "-m ytscout" in line]
    return [line.split("-m ytscout", 1)[1].strip() for line in lines]


def test_dry_run_lists_the_steps_in_order() -> None:
    result = _dry_run()
    assert result.returncode == 0, result.stderr
    commands = _commands(result.stdout)
    assert len(commands) == len(EXPECTED_STEPS)
    for command, expected in zip(commands, EXPECTED_STEPS, strict=True):
        assert command.startswith(expected), (command, expected)
    collectors = [c for c in commands if c.startswith("collect")]
    assert len(collectors) == 5
    for command in collectors:
        assert "--max-units 8000" in command, command
        assert "--resume" not in command
    assert commands[-1] == "dashboard"


def test_dry_run_resume_reaches_collectors_only() -> None:
    result = _dry_run("-Resume")
    assert result.returncode == 0, result.stderr
    for command in _commands(result.stdout):
        assert ("--resume" in command) == command.startswith("collect"), command


def test_install_task_parses_without_running() -> None:
    assert POWERSHELL is not None
    script = REPO / "scripts" / "install_task.ps1"
    check = (
        "$e = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$e); if ($e.Count) {{ $e; exit 1 }}"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", check],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _real_run(tmp_path: Path, fake_body: str) -> tuple[int, str]:
    """Run the non-dry path against a fake interpreter (a .cmd) and a temp log dir."""
    assert POWERSHELL is not None
    fake = tmp_path / "fake_python.cmd"
    fake.write_text("@echo off\r\necho fake %*\r\n" + fake_body + "exit /b 0\r\n", encoding="ascii")
    logs = tmp_path / "logs"
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO / "scripts" / "run_weekly.ps1"),
            "-Python",
            str(fake),
            "-LogDir",
            str(logs),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
    )
    log_files = list(logs.glob("weekly-*.log"))
    assert len(log_files) == 1
    return result.returncode, log_files[0].read_text(encoding="utf-8-sig")


def test_quota_exhausted_skips_collectors_still_scores_and_exits_3(tmp_path: Path) -> None:
    code, log = _real_run(
        tmp_path,
        'if "%3"=="collect" if "%4"=="--competitors" exit /b 3\r\nif "%3"=="analyse" exit /b 2\r\n',
    )
    assert code == 3
    assert "RUN   python -m ytscout collect --own" in log
    assert "EXIT  3  python -m ytscout collect --competitors" in log
    for skipped in ("--analytics", "--transcripts", "--niches"):
        assert f"SKIP  python -m ytscout collect {skipped}" in log
    assert "EXIT  2  python -m ytscout analyse --summaries" in log
    assert "EXIT  0  python -m ytscout score --competitors" in log
    assert log.rstrip().splitlines()[-2].endswith("EXIT  0  python -m ytscout dashboard")


def test_no_token_is_a_warning_other_failures_exit_1(tmp_path: Path) -> None:
    code, log = _real_run(
        tmp_path,
        'if "%4"=="--analytics" exit /b 4\r\nif "%3"=="score" exit /b 1\r\n',
    )
    assert code == 1
    assert "WARN  exit 4: no OAuth token" in log
    assert "ERROR exit 1; continuing" in log
    assert "RUN   python -m ytscout dashboard" in log


def test_clean_week_exits_0(tmp_path: Path) -> None:
    code, log = _real_run(tmp_path, "")
    assert code == 0
    assert log.count("EXIT  0") == len(EXPECTED_STEPS)
