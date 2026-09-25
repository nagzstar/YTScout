"""doctor (030): table shape, exit codes for required vs optional failures, no secrets out.

Every network or process probe is monkeypatched; nothing here reaches Google or ``claude``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ytscout import doctor
from ytscout.cli import EXIT_ERROR, EXIT_OK, main
from ytscout.settings import load
from ytscout.store import connect, default_db_path, repo
from ytscout.youtube import FakeTransport, Ledger

SECRET = "SECRET123"
CHANNEL = "UCdoctortest"

ROW_NAMES = [
    "Python >= 3.12",
    "settings.yaml and .env load",
    "data_dir writable",
    "DB opens, migrations current",
    "YT_API_KEY set",
    "TLS CA bundle",
    "Data API reachable",
    "client secret file present",
    "token present, refreshes, scopes right",
    "claude on PATH",
    "claude version >= 2.1.259",
    "claude logged in",
    "pipeline_repo_path exists",
    "pipeline_coverage.yaml present",
    "last weekly run",
    "quota today",
]
# The fixture replaces the probes; tests of the probes themselves call these originals.
REAL_PROBE_DATA_API = doctor.probe_data_api
REAL_PROBE_CLAUDE_LOGIN = doctor.probe_claude_login

NETWORK_ROWS = {"Data API reachable", "token present, refreshes, scopes right", "claude logged in"}


def _not_called(*_args, **_kwargs):
    raise AssertionError("a network probe ran under --offline")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo root with settings, a .env holding the fake key, and healthy probes."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    pipeline = tmp_path / "pipeline"
    pipeline.mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(
        f"own_channel_id: {CHANNEL}\npipeline_repo_path: {pipeline.as_posix()}\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "pipeline_coverage.yaml").write_text("steps: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text(f"YT_API_KEY={SECRET}\n", encoding="utf-8")
    for var in ("YT_API_KEY", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH", "YT_CHANNEL_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor, "claude_path", lambda: "C:/fake/claude.exe")
    monkeypatch.setattr(doctor, "claude_version", lambda: (2, 1, 281))
    monkeypatch.setattr(doctor, "probe_data_api", lambda *a: (doctor.PASS, "ok"))
    monkeypatch.setattr(doctor, "probe_token", lambda *a: (doctor.PASS, "ok"))
    monkeypatch.setattr(doctor, "probe_claude_login", lambda *a: (doctor.PASS, "ok"))
    return tmp_path


def _rows(report: doctor.Report) -> dict[str, doctor.Check]:
    return {c.name: c for c in report.checks}


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(["doctor", *argv])
    out, err = capsys.readouterr()
    return code, out + err


def test_table_lists_every_check_in_order(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, "--offline")
    assert code == EXIT_OK, out
    lines = out.splitlines()
    assert lines[0].split() == ["check", "required", "detail"]
    body = lines[1 : 1 + len(ROW_NAMES)]
    for line, name in zip(body, ROW_NAMES, strict=True):
        assert name in line
    assert lines[-1] == "doctor: ok"


def test_offline_skips_the_three_network_probes(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for probe in ("probe_data_api", "probe_token", "probe_claude_login"):
        monkeypatch.setattr(doctor, probe, _not_called)
    report = doctor.run_checks(offline=True)
    rows = _rows(report)
    assert [c.name for c in report.checks] == ROW_NAMES
    for name in NETWORK_ROWS:
        assert rows[name].status == doctor.SKIP
    expected_fail = {"last weekly run", "client secret file present"}  # none in tmp_path
    others = [c for c in report.checks if c.name not in NETWORK_ROWS | expected_fail]
    assert all(c.status in (doctor.PASS, doctor.INFO) for c in others), others
    assert report.ok


def test_online_runs_the_probes(root: Path) -> None:
    rows = _rows(doctor.run_checks(offline=False))
    for name in NETWORK_ROWS:
        assert rows[name].status == doctor.PASS


@pytest.mark.parametrize(
    "probe",
    ["probe_data_api", "probe_claude_login"],
)
def test_a_required_network_failure_exits_1(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], probe: str
) -> None:
    monkeypatch.setattr(doctor, probe, lambda *a: (doctor.FAIL, "down"))
    code, out = _run(capsys)
    assert code == EXIT_ERROR
    assert "doctor: 1 required check(s) failed" in out


def test_an_optional_failure_still_exits_0(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor, "probe_token", lambda *a: (doctor.FAIL, "no token file"))
    (root / "config" / "pipeline_coverage.yaml").unlink()
    (root / "pipeline").rmdir()
    code, out = _run(capsys)
    assert code == EXIT_OK, out
    report = doctor.run_checks(offline=False)
    rows = _rows(report)
    assert rows["token present, refreshes, scopes right"].status == doctor.FAIL
    assert rows["pipeline_repo_path exists"].status == doctor.FAIL
    assert rows["pipeline_coverage.yaml present"].status == doctor.FAIL
    assert rows["client secret file present"].status == doctor.FAIL
    assert report.ok


def test_claude_missing_is_required(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor, "claude_path", lambda: None)
    code, _ = _run(capsys, "--offline")
    assert code == EXIT_ERROR
    rows = _rows(doctor.run_checks(offline=True))
    assert rows["claude on PATH"].status == doctor.FAIL
    assert rows["claude version >= 2.1.259"].status == doctor.SKIP


def test_claude_too_old_is_required(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor, "claude_version", lambda: (2, 1, 258))
    code, out = _run(capsys, "--offline")
    assert code == EXIT_ERROR
    assert "2.1.258" in out


def test_missing_api_key_is_required(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (root / ".env").write_text("", encoding="utf-8")
    code, _ = _run(capsys, "--offline")
    assert code == EXIT_ERROR
    rows = _rows(doctor.run_checks(offline=False))
    assert rows["YT_API_KEY set"].status == doctor.FAIL
    assert rows["Data API reachable"].status == doctor.SKIP


def test_missing_settings_exits_1_and_skips_dependents(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (root / "config" / "settings.yaml").unlink()
    code, out = _run(capsys, "--offline")
    assert code == EXIT_ERROR
    assert "settings.example.yaml" in out
    rows = _rows(doctor.run_checks(offline=True))
    assert rows["settings.yaml and .env load"].status == doctor.FAIL
    assert rows["DB opens, migrations current"].status == doctor.SKIP
    assert rows["quota today"].status == doctor.SKIP


def test_a_crashing_probe_is_a_failure_not_a_traceback(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_a):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(doctor, "probe_data_api", boom)
    code, out = _run(capsys)
    assert code == EXIT_ERROR
    assert "RuntimeError: transport exploded" in out


@pytest.mark.parametrize("offline", [True, False])
def test_the_api_key_is_never_printed(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    offline: bool,
) -> None:
    # A googleapiclient error carries the request URL, key included; doctor must scrub it.
    def leaky(*_a):
        raise RuntimeError(f"<HttpError 400 when requesting ...&key={SECRET}&alt=json>")

    monkeypatch.setattr(doctor, "probe_data_api", leaky)
    monkeypatch.setattr(doctor, "probe_token", leaky)
    monkeypatch.setattr(doctor, "probe_claude_login", leaky)
    _, out = _run(capsys, *(["--offline"] if offline else []))
    assert "YT_API_KEY set" in out
    assert SECRET not in out
    assert SECRET[:6] not in out
    assert CHANNEL not in out  # own channel id: presence only


def test_last_run_and_quota_rows(root: Path) -> None:
    settings = load()
    conn = connect(default_db_path(settings))
    with conn:
        run_id = repo.start_run(conn, "collect_own")
        repo.finish_run(conn, run_id, "error")
    Ledger(conn, settings.quota.daily_cap).charge(7)
    conn.close()
    rows = _rows(doctor.run_checks(offline=True))
    assert rows["last weekly run"].status == doctor.FAIL
    assert "collect_own" in rows["last weekly run"].reason
    assert rows["last weekly run"].reason.endswith(": error")
    assert rows["quota today"].status == doctor.INFO
    assert rows["quota today"].reason.startswith("7 used, 8993 remaining of 9000")
    assert doctor.run_checks(offline=True).ok  # both optional


def test_probe_data_api_charges_exactly_one_unit(root: Path) -> None:
    settings = load()
    conn = connect(default_db_path(settings))
    ledger = Ledger(conn, settings.quota.daily_cap)
    before = ledger.used_today()
    transport = FakeTransport(
        {
            FakeTransport.key("channels", "list", part="id", id=CHANNEL, maxResults=50): {
                "items": [{"id": CHANNEL}]
            }
        }
    )
    status, reason = REAL_PROBE_DATA_API(settings, conn, None, transport)
    assert status == doctor.PASS, reason
    assert ledger.used_today() - before == 1
    assert len(transport.calls) == 1


def test_probe_data_api_fails_when_the_channel_is_unknown(root: Path) -> None:
    settings = load()
    conn = connect(default_db_path(settings))
    transport = FakeTransport(
        {FakeTransport.key("channels", "list", part="id", id=CHANNEL, maxResults=50): {}}
    )
    status, reason = REAL_PROBE_DATA_API(settings, conn, None, transport)
    assert status == doctor.FAIL
    assert CHANNEL not in reason


class _Proc:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_probe_claude_login_uses_the_subscription_flags(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        return _Proc(0, '{"type": "result", "is_error": false, "result": "ok"}')

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_" + "API_KEY", "must-be-stripped")
    status, _ = REAL_PROBE_CLAUDE_LOGIN(load())
    assert status == doctor.PASS
    argv = seen["argv"]
    assert argv[1:3] == ["-p", "reply with ok"]
    assert "--bare" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "ANTHROPIC_" + "API_KEY" not in seen["env"]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, ""), (0, '{"type": "result", "is_error": true, "subtype": "error"}'), (0, "nope")],
)
def test_probe_claude_login_failures(
    root: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str
) -> None:
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _Proc(returncode, stdout))
    status, _ = REAL_PROBE_CLAUDE_LOGIN(load())
    assert status == doctor.FAIL


def test_ascii_fallback_keeps_pass_and_fail_distinct(root: Path) -> None:
    report = doctor.run_checks(offline=True)
    table = doctor.format_table(report, ascii_only=True)
    table.encode("ascii")
    assert table.splitlines()[1].split()[:2] == ["ok", "Python"]


def test_doctor_does_not_import_collectors() -> None:
    code = (
        "import sys, ytscout.doctor; "
        "print(any(m.startswith('ytscout.collect') for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "False"
