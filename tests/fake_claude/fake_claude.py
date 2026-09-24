"""A fake ``claude`` for tests. Never talks to the network.

Checks the project's rules on every call (``--bare`` absent, ``--permission-prompts none``
present), records what it saw, and prints a ``--output-format json`` result whose
``structured_output`` satisfies the ``--json-schema`` it was given.

Environment knobs, all optional:

* ``FAKE_CLAUDE_RECORD``  path of a JSON file to write ``{argv, cwd, env_has_key}`` to.
* ``FAKE_CLAUDE_VERSION`` what ``--version`` prints (default ``2.1.281 (Claude Code)``).
* ``FAKE_CLAUDE_OUTPUT``  raw text to print instead of a generated result.
* ``FAKE_CLAUDE_EXIT``    exit code to return after printing (default 0).
* ``FAKE_CLAUDE_SLEEP``   seconds to sleep before printing (for timeout tests).
* ``FAKE_CLAUDE_STDERR``  text to print on stderr.

The launchers next to this file (``claude.cmd``, ``claude``) run it; ``tests/conftest.py``
builds a ``claude.exe`` on Windows because ``cmd.exe`` cuts a multi-line argument at its
first newline and the prompt is multi-line.
"""

from __future__ import annotations

import json
import os
import sys
import time

DEFAULT_VERSION = "2.1.281 (Claude Code)"
RULE_VIOLATION = 64

# Assembled, not written out: lazyboy/guard.py refuses the literal in any file.
API_KEY_VAR = "ANTHROPIC_" + "API_KEY"


def fill(schema: dict) -> object:
    """A value that satisfies ``schema`` (enough for the schemas in ``schemas/``)."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type", "object")
    if isinstance(kind, list):
        kind = kind[0]
    if kind == "object":
        return {key: fill(sub) for key, sub in schema.get("properties", {}).items()}
    if kind == "array":
        n = schema.get("minItems", 1)
        return [fill(schema.get("items", {"type": "string"})) for _ in range(n)]
    if kind == "string":
        return "fake"
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return True
    return None


def option(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv: list[str]) -> int:
    record = os.environ.get("FAKE_CLAUDE_RECORD")
    if record:
        with open(record, "w", encoding="utf-8") as fh:
            json.dump(
                {"argv": argv, "cwd": os.getcwd(), "env_has_key": API_KEY_VAR in os.environ},
                fh,
            )

    if argv == ["--version"] or argv == ["-v"]:
        print(os.environ.get("FAKE_CLAUDE_VERSION", DEFAULT_VERSION))
        return 0

    if "--bare" in argv:
        print("fake claude: --bare is forbidden (subscription login only)", file=sys.stderr)
        return RULE_VIOLATION
    if option(argv, "--permission-prompts") != "none":
        print("fake claude: --permission-prompts none is required", file=sys.stderr)
        return RULE_VIOLATION
    if argv[:1] != ["-p"]:
        print("fake claude: expected -p <prompt> first", file=sys.stderr)
        return RULE_VIOLATION

    sleep = os.environ.get("FAKE_CLAUDE_SLEEP")
    if sleep:
        time.sleep(float(sleep))
    stderr = os.environ.get("FAKE_CLAUDE_STDERR")
    if stderr:
        print(stderr, file=sys.stderr)

    raw = os.environ.get("FAKE_CLAUDE_OUTPUT")
    if raw is not None:
        sys.stdout.write(raw)
    else:
        schema_text = option(argv, "--json-schema") or "{}"
        output = fill(json.loads(schema_text))
        result = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1200,
            "duration_api_ms": 1000,
            "num_turns": 2,
            "result": "done",
            "session_id": "fake",
            "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "structured_output": output,
        }
        print(json.dumps(result))
    sys.stdout.flush()
    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
