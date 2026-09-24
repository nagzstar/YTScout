"""Shared fixtures. ``fake_claude`` puts a fake ``claude`` first on PATH.

The fake is ``tests/fake_claude/fake_claude.py``. On POSIX the ``claude`` shell shim next
to it runs it. On Windows a ``.cmd`` shim cannot: ``cmd.exe`` cuts a multi-line argument
at its first newline and the prompt argument is multi-line. So the fixture builds a real
``claude.exe`` from the console launcher pip vendors (``distlib``'s ``t64.exe``): the
launcher runs ``python.exe <itself> <args>``, Python treats the exe as a zipapp and runs
the ``__main__.py`` appended to it, which hands over to ``fake_claude.py``. Nothing is
compiled and nothing is committed; the exe lives in ``tmp_path``.
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


def build_windows_exe(target: Path) -> None:
    """``target`` (``claude.exe``) = distlib launcher + ``#!python`` + zip(``__main__.py``)."""
    main_py = (
        "import runpy, sys\n"
        f"sys.argv[0] = {str(FAKE_SCRIPT)!r}\n"
        f"runpy.run_path({str(FAKE_SCRIPT)!r}, run_name='__main__')\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("__main__.py", main_py)
    shebang = f'#!"{sys.executable}"\r\n'.encode()
    target.write_bytes(_launcher().read_bytes() + shebang + buf.getvalue())


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
    bin_dir = tmp_path / "fake-claude-bin"
    bin_dir.mkdir()
    shutil.copy(FAKE_SCRIPT, bin_dir / FAKE_SCRIPT.name)
    if sys.platform == "win32":
        shutil.copy(FAKE_DIR / "claude.cmd", bin_dir / "claude.cmd")
        build_windows_exe(bin_dir / "claude.exe")
    else:
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
