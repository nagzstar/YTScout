"""Shared fixtures. ``fake_claude`` puts a fake ``claude`` first on PATH.

The fake is ``tests/fake_claude/fake_claude.py``. On POSIX the ``claude`` shell shim next
to it runs it. On Windows a ``.cmd`` shim cannot: ``cmd.exe`` cuts a multi-line argument
at its first newline and the prompt argument is multi-line. So the fixture builds a real
``claude.exe`` from the console launcher pip vendors (``distlib``'s ``t64.exe``): the
launcher runs ``python.exe <itself> <args>``, Python treats the exe as a zipapp and runs
the ``__main__.py`` appended to it, which hands over to ``fake_claude.py``. Nothing is
compiled and nothing is committed; the exe lives in ``tests/fake_claude/build/`` (gitignored).

The exe is built **once**, with deterministic bytes, into one stable folder rather than into
each test's ``tmp_path``. Antivirus (Avast CyberCapture) holds every never-seen executable
for cloud analysis; one fresh exe per test meant a dozen holds per run and killed sessions.
One stable file has one hash, which the AV learns once. Per-test state travels in env vars,
so sharing the folder between tests is safe.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

FAKE_DIR = Path(__file__).parent / "fake_claude"
FAKE_SCRIPT = FAKE_DIR / "fake_claude.py"
BUILD_DIR = FAKE_DIR / "build"  # covered by the ``build/`` rule in .gitignore


def _launcher() -> Path:
    from pip._vendor import distlib  # noqa: PLC0415  (test-only, pip is always in the venv)

    name = (
        "t64-arm.exe"
        if "arm" in os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
        else ("t64.exe" if sys.maxsize > 2**32 else "t32.exe")
    )
    path = Path(distlib.__file__).parent / name
    if not path.is_file():
        raise RuntimeError(f"pip's distlib launcher {name} not found at {path}")
    return path


def windows_exe_bytes() -> bytes:
    """``claude.exe`` = distlib launcher + ``#!python`` + zip(``__main__.py``), byte-stable."""
    main_py = (
        "import runpy, sys\n"
        f"sys.argv[0] = {str(FAKE_SCRIPT)!r}\n"
        f"runpy.run_path({str(FAKE_SCRIPT)!r}, run_name='__main__')\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Fixed timestamp: same inputs must give the same bytes, so the AV sees one hash.
        zf.writestr(zipfile.ZipInfo("__main__.py", date_time=(2020, 1, 1, 0, 0, 0)), main_py)
    shebang = f'#!"{sys.executable}"\r\n'.encode()
    return _launcher().read_bytes() + shebang + buf.getvalue()


def windows_bin_dir() -> Path:
    """The stable fake-``claude`` folder for Windows, (re)built only when its bytes would change."""
    BUILD_DIR.mkdir(exist_ok=True)
    exe = BUILD_DIR / "claude.exe"
    wanted = windows_exe_bytes()
    if not exe.is_file() or exe.read_bytes() != wanted:
        exe.write_bytes(wanted)
    for name in ("claude.cmd", FAKE_SCRIPT.name):
        shutil.copy(FAKE_DIR / name, BUILD_DIR / name)
    return BUILD_DIR


@dataclass
class FakeClaude:
    bin_dir: Path
    record_path: Path

    def record(self) -> dict:
        """What the last call saw: ``argv``, ``cwd``, ``env_has_key``."""
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    @property
    def called(self) -> bool:
        return self.record_path.is_file()


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    """A fake ``claude`` first on PATH, recording each call to ``record_path``."""
    if sys.platform == "win32":
        bin_dir = windows_bin_dir()
    else:
        bin_dir = tmp_path / "fake-claude-bin"
        bin_dir.mkdir()
        shutil.copy(FAKE_SCRIPT, bin_dir / FAKE_SCRIPT.name)
        shutil.copy(FAKE_DIR / "claude", bin_dir / "claude")
        (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CLAUDE_PYTHON", sys.executable)
    record = tmp_path / "fake-claude-record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    for name in ("FAKE_CLAUDE_VERSION", "FAKE_CLAUDE_OUTPUT", "FAKE_CLAUDE_EXIT"):
        monkeypatch.delenv(name, raising=False)
    for name in ("FAKE_CLAUDE_SLEEP", "FAKE_CLAUDE_STDERR"):
        monkeypatch.delenv(name, raising=False)
    return FakeClaude(bin_dir=bin_dir, record_path=record)
