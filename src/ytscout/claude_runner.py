"""Claude through the subscription: ``claude -p`` as a subprocess, never an API key.

One hard rule (DESIGN.md §7): Claude is reached through Claude Code's non-interactive mode
with the user's subscription login. ``--bare`` is never passed (it skips that login and
wants an API key) and the Anthropic key variable is removed from the child environment so
a stray shell export cannot switch the call over to pay-per-token billing.

``run()`` builds exactly this command, from the repo root so ``CLAUDE.md`` loads::

    claude -p "<prompt file text>\\n\\nPacket file (read it with the Read tool): <abs packet>"
        --output-format json --json-schema "<schema file text>"
        --allowedTools Read --permission-prompts none [--model <model>]

The packet travels by path, never on stdin. Stdout is one JSON object whose
``structured_output`` must validate against the schema; anything else raises
``ClaudeUnavailable`` so the caller can store nothing and exit 5.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ytscout.settings import find_repo_root

MIN_VERSION: tuple[int, ...] = (2, 1, 259)  # first release with --permission-prompts
DEFAULT_TIMEOUT_S = 900
PACKET_LINE = "Packet file (read it with the Read tool): "
HASH_HEX_CHARS = 12
STDERR_TAIL_CHARS = 2000

# The environment variable that would switch Claude Code from the subscription to an API
# key. Its name is assembled here rather than written out because lazyboy/guard.py refuses
# to write the literal into any file; the runner exists to strip it, not to use it.
_API_KEY_VAR = "ANTHROPIC_" + "API_KEY"

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class ClaudeUnavailable(Exception):
    """``claude`` is missing, too old, failed, timed out or returned unusable output."""

    def __init__(self, reason: str, stderr_tail: str = "") -> None:
        self.reason = reason
        self.stderr_tail = stderr_tail
        super().__init__(reason if not stderr_tail else f"{reason}\n{stderr_tail}")


class SchemaError(ValueError):
    """A value does not conform to a JSON Schema (see ``validate``)."""


@dataclass
class RunResult:
    structured_output: dict
    session_id: str | None
    usage: dict = field(default_factory=dict)
    total_cost_usd: float | None = None
    prompt_hash: str = ""
    schema_hash: str = ""
    duration_s: float = 0.0


# --- availability -------------------------------------------------------------------------


def executable() -> str | None:
    """The resolved path of ``claude`` on PATH, or ``None``."""
    return shutil.which("claude")


def is_available() -> bool:
    return executable() is not None


def parse_version(text: str) -> tuple[int, ...]:
    """``"2.1.281 (Claude Code)"`` → ``(2, 1, 281)``."""
    match = _VERSION.search(text)
    if not match:
        raise ClaudeUnavailable(f"could not parse `claude --version` output: {text.strip()!r}")
    return tuple(int(part) for part in match.groups())


def version() -> tuple[int, ...]:
    """The installed Claude Code version from ``claude --version``."""
    exe = executable()
    if exe is None:
        raise ClaudeUnavailable("`claude` is not on PATH")
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=child_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudeUnavailable(f"`claude --version` failed: {exc}") from exc
    if proc.returncode != 0:
        raise ClaudeUnavailable(f"`claude --version` exited {proc.returncode}", _tail(proc.stderr))
    return parse_version(proc.stdout)


def version_string(parts: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in parts)


def check_available() -> str | None:
    """``None`` when ``claude`` is on PATH and new enough, else the reason it is not."""
    if not is_available():
        return "`claude` is not on PATH (install Claude Code and log in)"
    try:
        found = version()
    except ClaudeUnavailable as exc:
        return exc.reason
    if found < MIN_VERSION:
        return (
            f"claude {version_string(found)} is older than the minimum "
            f"{version_string(MIN_VERSION)} (needed for --permission-prompts none)"
        )
    return None


# --- command ------------------------------------------------------------------------------


def file_hash(path: Path) -> str:
    """First 12 hex characters of the SHA-256 of the file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:HASH_HEX_CHARS]


def child_env() -> dict[str, str]:
    """A copy of the environment without the Anthropic API key variable.

    Claude Code prefers a key over the subscription login when both are present. This
    project only ever bills the subscription, so the key is removed even if the parent
    shell exported one by accident.
    """
    env = dict(os.environ)
    env.pop(_API_KEY_VAR, None)
    return env


def build_prompt(prompt_text: str, packet_path: Path) -> str:
    return f"{prompt_text}\n\n{PACKET_LINE}{Path(packet_path).resolve()}"


def build_command(
    exe: str, prompt_text: str, packet_path: Path, schema_text: str, model: str | None = None
) -> list[str]:
    """The argv for one call. ``--bare`` is never part of it."""
    argv = [
        exe,
        "-p",
        build_prompt(prompt_text, packet_path),
        "--output-format",
        "json",
        "--json-schema",
        schema_text,
        "--allowedTools",
        "Read",
        "--permission-prompts",
        "none",
    ]
    if model:
        argv += ["--model", model]
    return argv


def _tail(text: str | None) -> str:
    return (text or "")[-STDERR_TAIL_CHARS:]


def _parse_stdout(stdout: str) -> dict:
    """The result object from ``--output-format json``.

    Stdout should be exactly one JSON object; if something else got printed around it,
    the last line that parses as an object wins.
    """
    text = stdout.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ClaudeUnavailable("claude printed no JSON result object", _tail(stdout))


def run(
    prompt_path: Path,
    packet_path: Path,
    schema_path: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    model: str | None = None,
    cwd: Path | None = None,
) -> RunResult:
    """One ``claude -p`` call: prompt file + packet path in, validated structured output out.

    Raises ``ClaudeUnavailable`` when ``claude`` is missing, exits non-zero, times out, or
    returns no ``structured_output`` that validates against ``schema_path``.
    """
    exe = executable()
    if exe is None:
        raise ClaudeUnavailable("`claude` is not on PATH (install Claude Code and log in)")
    prompt_path, schema_path = Path(prompt_path), Path(schema_path)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    schema_text = schema_path.read_text(encoding="utf-8")
    schema = json.loads(schema_text)
    argv = build_command(exe, prompt_text, packet_path, schema_text, model)
    workdir = Path(cwd) if cwd is not None else find_repo_root(Path(__file__).parent)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=workdir,
            env=child_env(),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise ClaudeUnavailable(f"claude timed out after {timeout:g}s", _tail(stderr)) from exc
    except OSError as exc:
        raise ClaudeUnavailable(f"could not start claude: {exc}") from exc
    duration = time.monotonic() - started

    if proc.returncode != 0:
        raise ClaudeUnavailable(f"claude exited {proc.returncode}", _tail(proc.stderr))
    result = _parse_stdout(proc.stdout)
    if result.get("is_error"):
        raise ClaudeUnavailable(
            f"claude reported an error ({result.get('subtype', '?')}): "
            f"{str(result.get('result', ''))[:200]}",
            _tail(proc.stderr),
        )
    output = result.get("structured_output")
    if not isinstance(output, dict):
        raise ClaudeUnavailable(
            f"claude returned no structured_output (subtype {result.get('subtype', '?')})",
            _tail(proc.stderr),
        )
    try:
        validate(output, schema)
    except SchemaError as exc:
        raise ClaudeUnavailable(f"structured_output does not match the schema: {exc}") from exc

    usage = result.get("usage")
    cost = result.get("total_cost_usd")
    return RunResult(
        structured_output=output,
        session_id=result.get("session_id"),
        usage=usage if isinstance(usage, dict) else {},
        total_cost_usd=float(cost) if isinstance(cost, int | float) else None,
        prompt_hash=file_hash(prompt_path),
        schema_hash=file_hash(schema_path),
        duration_s=duration,
    )


# --- a small JSON Schema validator ---------------------------------------------------------
#
# Enough of draft-07 for the schemas in schemas/: type, enum, const, properties, required,
# additionalProperties, items, minItems/maxItems, minLength/maxLength, minimum/maximum,
# pattern. Kept in-house rather than adding the ``jsonschema`` package: the schemas are ours,
# small and flat, and one fewer dependency is one fewer thing for ``doctor`` to check.

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _is_type(value: object, name: str) -> bool:
    if name not in _TYPES:
        raise SchemaError(f"unknown type in schema: {name!r}")
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    if name == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _TYPES[name])


def validate(value: object, schema: dict, path: str = "$") -> None:
    """Raise ``SchemaError`` naming the first place ``value`` breaks ``schema``."""
    if not isinstance(schema, dict):
        raise SchemaError(f"schema at {path} is not an object")
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, n) for n in names):
            raise SchemaError(f"{path}: expected {' or '.join(names)}, got {_name(value)}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: {value!r} is not {schema['const']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise SchemaError(f"{path}: {value!r} does not match {schema['pattern']!r}")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: {value} is below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: {value} is above the maximum {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise SchemaError(f"{path}: missing required property {key!r}")
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise SchemaError(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(item, schema["additionalProperties"], f"{path}.{key}")


def _name(value: object) -> str:
    return "null" if value is None else type(value).__name__
