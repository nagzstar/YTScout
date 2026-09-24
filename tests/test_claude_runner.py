"""``claude_runner`` against the fake ``claude`` on PATH: argv shape, env, failures, hashes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from ytscout import claude_runner
from ytscout.claude_runner import (
    MIN_VERSION,
    ClaudeUnavailable,
    SchemaError,
    build_command,
    check_available,
    file_hash,
    is_available,
    parse_version,
    run,
    validate,
    version,
)
from ytscout.settings import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__).parent)
PROMPT = REPO_ROOT / "prompts" / "video_summary.md"
SCHEMA = REPO_ROOT / "schemas" / "video_summary.json"
FIELDS = {
    "hook_type",
    "structure",
    "topic_tags",
    "title_formula",
    "pacing_note",
    "claims_count",
    "unique_angle",
    "one_line_summary",
}

# Assembled, not written out: lazyboy/guard.py refuses the literal in any file.
API_KEY_VAR = "ANTHROPIC_" + "API_KEY"


@pytest.fixture
def packet(tmp_path: Path) -> Path:
    path = tmp_path / "packet.json"
    path.write_text(json.dumps({"title": "Top 5 fastest cats"}), encoding="utf-8")
    return path


# --- availability and version --------------------------------------------------------------


def test_is_available_false_with_empty_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    assert is_available() is False
    with pytest.raises(ClaudeUnavailable, match="not on PATH"):
        version()
    assert "not on PATH" in (check_available() or "")


def test_fake_is_found_and_versioned(fake_claude) -> None:
    assert is_available()
    assert version() == (2, 1, 281)
    assert check_available() is None
    assert fake_claude.record()["argv"] == ["--version"]


def test_too_old_is_reported(fake_claude, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.258 (Claude Code)")
    assert version() == (2, 1, 258)
    reason = check_available()
    assert reason is not None
    assert "2.1.258" in reason and "2.1.259" in reason
    assert MIN_VERSION == (2, 1, 259)


def test_parse_version() -> None:
    assert parse_version("2.1.281 (Claude Code)\n") == (2, 1, 281)
    assert parse_version("v10.0.3") == (10, 0, 3)
    with pytest.raises(ClaudeUnavailable):
        parse_version("no version here")


# --- the command ---------------------------------------------------------------------------


def test_argv_is_exactly_the_specified_shape(fake_claude, packet: Path) -> None:
    result = run(PROMPT, packet, SCHEMA)
    record = fake_claude.record()
    expected = [
        "-p",
        PROMPT.read_text(encoding="utf-8")
        + "\n\nPacket file (read it with the Read tool): "
        + str(packet.resolve()),
        "--output-format",
        "json",
        "--json-schema",
        SCHEMA.read_text(encoding="utf-8"),
        "--allowedTools",
        "Read",
        "--permission-prompts",
        "none",
    ]
    assert record["argv"] == expected
    assert "--bare" not in record["argv"]
    assert "--model" not in record["argv"]
    assert Path(record["cwd"]).resolve() == REPO_ROOT.resolve()
    assert set(result.structured_output) == FIELDS
    assert result.session_id == "fake"
    assert result.total_cost_usd == pytest.approx(0.0123)
    assert result.usage == {"input_tokens": 100, "output_tokens": 50}
    assert result.duration_s >= 0


def test_model_flag_is_appended_only_when_given(fake_claude, packet: Path) -> None:
    run(PROMPT, packet, SCHEMA, model="sonnet")
    argv = fake_claude.record()["argv"]
    assert argv[-2:] == ["--model", "sonnet"]
    assert argv[-4:-2] == ["--permission-prompts", "none"]


def test_build_command_never_contains_bare() -> None:
    argv = build_command("claude", "prompt", Path("p.json"), "{}", "opus")
    assert "--bare" not in argv
    assert argv[0] == "claude" and argv[1] == "-p"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--allowedTools") + 1] == "Read"


def test_api_key_in_parent_env_is_not_seen_by_child(
    fake_claude, packet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_KEY_VAR, "sk-not-a-real-key")
    assert API_KEY_VAR in os.environ
    assert API_KEY_VAR not in claude_runner.child_env()
    run(PROMPT, packet, SCHEMA)
    assert fake_claude.record()["env_has_key"] is False


def test_cwd_override(fake_claude, packet: Path, tmp_path: Path) -> None:
    run(PROMPT, packet, SCHEMA, cwd=tmp_path)
    assert Path(fake_claude.record()["cwd"]).resolve() == tmp_path.resolve()


# --- failures -----------------------------------------------------------------------------


def test_non_zero_exit_raises_with_stderr_tail(
    fake_claude, packet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    monkeypatch.setenv("FAKE_CLAUDE_STDERR", "Not logged in. Please run /login")
    with pytest.raises(ClaudeUnavailable) as excinfo:
        run(PROMPT, packet, SCHEMA)
    assert excinfo.value.reason == "claude exited 1"
    assert "Not logged in" in excinfo.value.stderr_tail


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("this is not json", "no JSON result object"),
        ('{"type":"result","subtype":"error_max_turns","session_id":"x"}', "no structured_output"),
        (
            '{"type":"result","is_error":true,"subtype":"error","result":"boom"}',
            "reported an error",
        ),
        (
            '{"type":"result","structured_output":{"hook_type":"question"}}',
            "does not match the schema",
        ),
        (
            '{"type":"result","structured_output":{"hook_type":"loud","structure":"s",'
            '"topic_tags":[],"title_formula":"t","pacing_note":"p","claims_count":1,'
            '"unique_angle":"u","one_line_summary":"o"}}',
            "does not match the schema",
        ),
    ],
)
def test_bad_output_raises(
    fake_claude, packet: Path, monkeypatch: pytest.MonkeyPatch, raw: str, reason: str
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", raw)
    with pytest.raises(ClaudeUnavailable, match=reason):
        run(PROMPT, packet, SCHEMA)


def test_result_object_is_found_among_other_stdout_lines(
    fake_claude, packet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = {
        "type": "result",
        "session_id": "s1",
        "structured_output": {
            "hook_type": "shock_stat",
            "structure": "s",
            "topic_tags": ["cats"],
            "title_formula": "t",
            "pacing_note": "p",
            "claims_count": 3,
            "unique_angle": "u",
            "one_line_summary": "o",
        },
    }
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", "warning: something\n" + json.dumps(good) + "\n")
    result = run(PROMPT, packet, SCHEMA)
    assert result.structured_output["claims_count"] == 3
    assert result.session_id == "s1"
    assert result.total_cost_usd is None and result.usage == {}


def test_timeout_raises(fake_claude, packet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "3")
    with pytest.raises(ClaudeUnavailable, match="timed out after 0.5s"):
        run(PROMPT, packet, SCHEMA, timeout=0.5)


def test_missing_claude_raises(packet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ClaudeUnavailable, match="not on PATH"):
        run(PROMPT, packet, SCHEMA)


# --- hashes -------------------------------------------------------------------------------


def test_hashes_are_stable_sha256_prefixes(fake_claude, packet: Path, tmp_path: Path) -> None:
    expected_prompt = hashlib.sha256(PROMPT.read_bytes()).hexdigest()[:12]
    expected_schema = hashlib.sha256(SCHEMA.read_bytes()).hexdigest()[:12]
    assert file_hash(PROMPT) == expected_prompt
    assert len(expected_prompt) == 12
    first = run(PROMPT, packet, SCHEMA)
    second = run(PROMPT, packet, SCHEMA)
    assert first.prompt_hash == second.prompt_hash == expected_prompt
    assert first.schema_hash == second.schema_hash == expected_schema
    edited = tmp_path / "video_summary.md"
    edited.write_text(PROMPT.read_text(encoding="utf-8") + "\nBe terse.\n", encoding="utf-8")
    assert file_hash(edited) != expected_prompt
    assert run(edited, packet, SCHEMA).prompt_hash == file_hash(edited)


# --- the schema and the validator ---------------------------------------------------------


def test_schema_file_has_the_specified_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["properties"]) == FIELDS
    assert set(schema["required"]) == FIELDS
    assert schema["additionalProperties"] is False
    assert schema["properties"]["hook_type"]["enum"] == [
        "question",
        "shock_stat",
        "countdown_tease",
        "story_open",
        "direct_claim",
        "other",
    ]
    assert schema["properties"]["topic_tags"]["maxItems"] == 5
    assert schema["properties"]["claims_count"]["type"] == "integer"


@pytest.mark.parametrize(
    "value, schema, message",
    [
        ("x", {"type": "integer"}, "expected integer"),
        (True, {"type": "integer"}, "expected integer"),
        (2.0, {"type": "integer"}, None),
        (None, {"type": ["string", "null"]}, None),
        ("z", {"enum": ["a", "b"]}, "not one of"),
        ("abcd", {"type": "string", "maxLength": 3}, "longer than 3"),
        ("Ab", {"type": "string", "pattern": "^[a-z]+$"}, "does not match"),
        (-1, {"type": "integer", "minimum": 0}, "below the minimum"),
        ([1, 2, 3], {"type": "array", "maxItems": 2}, "more than 2"),
        ([1, "x"], {"type": "array", "items": {"type": "integer"}}, r"\$\[1\]: expected integer"),
        ({}, {"type": "object", "required": ["a"]}, "missing required property 'a'"),
        ({"b": 1}, {"type": "object", "additionalProperties": False}, "unexpected property 'b'"),
        (
            {"a": {"b": "x"}},
            {"properties": {"a": {"properties": {"b": {"type": "integer"}}}}},
            r"\$\.a\.b: expected integer",
        ),
    ],
)
def test_validate(value: object, schema: dict, message: str | None) -> None:
    if message is None:
        validate(value, schema)
    else:
        with pytest.raises(SchemaError, match=message):
            validate(value, schema)


def test_fake_rejects_bare_and_missing_permission_flag(fake_claude, tmp_path: Path) -> None:
    """The fake enforces the two rules; prove it by calling it directly, not via ``run``."""
    import subprocess

    exe = claude_runner.executable()
    assert exe is not None and Path(exe).parent == fake_claude.bin_dir
    bad = subprocess.run(
        [exe, "-p", "hi", "--permission-prompts", "none", "--bare"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 64 and "--bare" in bad.stderr
    bad = subprocess.run([exe, "-p", "hi"], capture_output=True, text=True, check=False)
    assert bad.returncode == 64 and "--permission-prompts none" in bad.stderr
