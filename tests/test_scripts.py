"""scripts/run_weekly.ps1: dry-run step list, exit-code handling, and a log that survives locks."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
    "score --competitors",
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


def test_dry_run_runs_python_unbuffered() -> None:
    """038: -u, so a 30-minute collect writes its per-video lines to the log as it goes."""
    result = _dry_run()
    assert result.returncode == 0, result.stderr
    lines = [line.strip() for line in result.stdout.splitlines() if "-m ytscout" in line]
    assert len(lines) == len(EXPECTED_STEPS)
    for line in lines:
        assert line.startswith(".venv\\Scripts\\python.exe -u -m ytscout "), line
    text = (REPO / "scripts" / "run_weekly.ps1").read_text(encoding="utf-8")
    assert "$env:PYTHONUNBUFFERED = '1'" in text


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


def test_install_task_adds_a_tuesday_resume_trigger() -> None:
    """032: a second trigger after the 08:00 UK reset; -Resume is on the (shared) action."""
    text = (REPO / "scripts" / "install_task.ps1").read_text(encoding="utf-8")
    assert "[string]$ResumeAt = '09:00'" in text
    assert "$ResumeDay = [System.DayOfWeek]::Tuesday" in text
    assert "-DaysOfWeek $ResumeDay -At $resumeTime" in text
    assert '-File `"$Script`" -Resume"' in text


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
        'if "%4"=="collect" if "%5"=="--competitors" exit /b 3\r\nif "%4"=="analyse" exit /b 2\r\n',
    )
    assert code == 3
    assert "RUN   python -u -m ytscout collect --own" in log
    assert "EXIT  3  python -u -m ytscout collect --competitors" in log
    for skipped in ("--analytics", "--transcripts", "--niches"):
        assert f"SKIP  python -u -m ytscout collect {skipped}" in log
    assert "EXIT  2  python -u -m ytscout analyse --summaries" in log
    assert "EXIT  0  python -u -m ytscout score --all" in log
    assert log.rstrip().splitlines()[-2].endswith("EXIT  0  python -u -m ytscout dashboard")


def test_no_token_is_a_warning_other_failures_exit_1(tmp_path: Path) -> None:
    code, log = _real_run(
        tmp_path,
        'if "%5"=="--analytics" exit /b 4\r\nif "%4"=="score" exit /b 1\r\n',
    )
    assert code == 1
    assert "WARN  exit 4: no OAuth token" in log
    assert "ERROR exit 1; continuing" in log
    assert "RUN   python -u -m ytscout dashboard" in log


def test_clean_week_exits_0(tmp_path: Path) -> None:
    code, log = _real_run(tmp_path, "")
    assert code == 0
    assert log.count("EXIT  0") == len(EXPECTED_STEPS)


# A fake interpreter for the lock tests: prints numbered lines, and on the transcripts
# step opens the log itself (as a viewer would, via YTSCOUT_RUN_LOG) with the given
# share mode while it keeps printing, then lets go.
_LOCKER = r"""
import ctypes, os, sys, time
from ctypes import wintypes

args = sys.argv[1:]
step = " ".join(args[3:5])
share = int(os.environ["LOCK_SHARE"])
hold = float(os.environ["LOCK_HOLD"])
handle = None
if step == "collect --transcripts":
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    GENERIC_READ, OPEN_EXISTING = 0x80000000, 3
    handle = k32.CreateFileW(os.environ["YTSCOUT_RUN_LOG"], GENERIC_READ, share, None,
                             OPEN_EXISTING, 0, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    print("locked", flush=True)
for i in range(5):
    print(f"line {step} {i}", flush=True)
    if handle is not None:
        time.sleep(hold / 5)
if handle is not None:
    k32.CloseHandle(handle)
print(f"done {step}", flush=True)
"""


def _locked_run(tmp_path: Path, share: int, hold: float) -> tuple[int, str, str]:
    assert POWERSHELL is not None
    helper = tmp_path / "locker.py"
    helper.write_text(_LOCKER, encoding="utf-8")
    fake = tmp_path / "fake_python.cmd"
    fake.write_text(
        f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="ascii",
    )
    logs = tmp_path / "logs"
    env = {**os.environ, "LOCK_SHARE": str(share), "LOCK_HOLD": str(hold)}
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
        timeout=180,
        cwd=REPO,
        env=env,
    )
    log_files = list(logs.glob("weekly-*.log"))
    assert len(log_files) == 1
    return result.returncode, log_files[0].read_text(encoding="utf-8-sig"), result.stdout


def _assert_nothing_lost(code: int, log: str, stdout: str) -> None:
    assert code == 0, stdout
    assert "locked" in log  # the lock really was taken while the script ran
    logged = [line[21:] for line in log.splitlines()]
    printed = [line[21:] for line in stdout.splitlines() if line[:2] == "20"]
    assert logged == printed  # every line, in order
    for step in EXPECTED_STEPS:
        assert f"RUN   python -u -m ytscout {step}" in log
    for i in range(5):
        assert f"line collect --transcripts {i}" in log
    assert log.count("EXIT  0") == len(EXPECTED_STEPS)
    assert log.rstrip().splitlines()[-1].split("  ", 1)[1].startswith("DONE  exit 0")


FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2


def test_log_survives_a_shared_reader_like_tail(tmp_path: Path) -> None:
    """038: a viewer holding the log open (read + share read/write) costs no lines."""
    code, log, stdout = _locked_run(tmp_path, FILE_SHARE_READ | FILE_SHARE_WRITE, 1.0)
    _assert_nothing_lost(code, log, stdout)


def test_log_survives_an_exclusive_reader(tmp_path: Path) -> None:
    """038: even a reader that denies writers for a few seconds only delays the lines."""
    code, log, stdout = _locked_run(tmp_path, 0, 3.0)
    _assert_nothing_lost(code, log, stdout)
