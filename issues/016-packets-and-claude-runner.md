# 016 — Packets, `claude_runner`, and the per-video summary call

**Type**: AFK
**Blocked by**: 015
**Add dirs**: none
**Covers**: src/ytscout/claude_runner.py, src/ytscout/packets.py, prompts/video_summary.md, schemas/video_summary.json, src/ytscout/cli.py (analyse, packet), tests/test_claude_runner.py, tests/test_packets.py, tests/fake_claude/
**Milestone**: M2

## Why

This is the LLM layer, and it has one hard rule: Claude is reached through Nagz's
subscription via `claude -p`, never an API key. Get the wrapper right once and every
prompt after this is a Markdown file and a schema.

## Scope

- `claude_runner.py`:
  - `is_available() -> bool` (`shutil.which("claude")`).
  - `version() -> tuple` from `claude --version`; `MIN_VERSION = (2, 1, 259)`.
  - `run(prompt_path, packet_path, schema_path, *, timeout=900, model=None) -> RunResult`
    where `RunResult` has `structured_output: dict`, `session_id`, `usage: dict`,
    `total_cost_usd: float | None`, `prompt_hash`, `schema_hash`, `duration_s`.
  - Command, exactly:
    `claude -p "<prompt file text>\n\nPacket file (read it with the Read tool): <abs packet path>"
    --output-format json --json-schema "<schema file text>" --allowedTools Read
    --permission-prompts none [--model <model>]`.
    Never `--bare`. `cwd` = repo root so `CLAUDE.md` loads. `env` = a copy of `os.environ`
    with `ANTHROPIC_API_KEY` **removed** if present, with a comment saying why.
  - Parse stdout as JSON; `structured_output` must be present and validate against the
    schema (use a small in-house validator or `jsonschema` if you add it to deps — your
    call, note it). Non-zero exit, timeout, or missing/invalid output → raise
    `ClaudeUnavailable(reason, stderr_tail)`.
  - `prompt_hash` / `schema_hash` = first 12 hex of sha256 of the file bytes.
- `packets.py`:
  - `video_packet(conn, video_id) -> dict`: channel title + subs, title, description
    (≤ 1,000 chars), tags, duration_s, is_short, published_at, latest views/likes/comments,
    transcript text (≤ 6,000 chars, truncated with `…[truncated]`) or `null` + status.
  - `write_packet(kind, payload) -> Path` → `data/packets/YYYY-MM-DD-<kind>-<n>.json`.
  - `ytscout packet --video <id>` writes one and prints the path (a debugging aid).
- `prompts/video_summary.md` and `schemas/video_summary.json`: fields `hook_type` (enum:
  question, shock_stat, countdown_tease, story_open, direct_claim, other), `structure`
  (short free text), `topic_tags` (≤ 5 slugs), `title_formula` (short), `pacing_note`,
  `claims_count` (int), `unique_angle`, `one_line_summary`. The prompt says: read the
  packet file, answer only from it, do not browse.
- `ytscout analyse --summaries [--limit N]`: for videos of own + approved/watch channels
  with a transcript row and no `video_summaries` row for the current `prompt_hash`, newest
  first, default `--limit 40`: build packet → `run` → write `video_summaries`. If
  `is_available()` is false or the version is too old: print why, exit **5**, write
  nothing (the weekly script logs and continues).
- `tests/fake_claude/`: a fake `claude` for tests — a Python script plus a `claude.cmd`
  shim (Windows) and a `claude` shell shim (POSIX) that: asserts `--bare` is absent and
  `--permission-prompts none` present, records argv to a file, reads the schema arg, and
  prints a valid `{"type":"result","structured_output":{…},"session_id":"fake",…}`. Tests
  prepend its dir to `PATH`.
- Tests: argv shape exactly as specified; `ANTHROPIC_API_KEY` set in the test env is not
  seen by the fake; invalid output raises `ClaudeUnavailable`; `prompt_hash` is stable;
  packet truncation lengths; `analyse --summaries` skips videos already summarised under
  the same hash and re-does them under a new hash.
- **Real calls allowed**: up to **10** real `claude -p` runs to prove it end to end, if
  `claude` is on PATH and logged in. Use the smallest packets available.

## Out of scope

- The competitor comparison prompt (017). Niche prompts (021, 024).

## Acceptance criteria

- [ ] `pytest -q` passes, including `tests/test_claude_runner.py` and `tests/test_packets.py`.
- [ ] A test asserts the literal absence of `--bare` in argv and the absence of
      `ANTHROPIC_API_KEY` in the child environment.
- [ ] `grep -rn "anthropic" pyproject.toml src/` finds nothing — no SDK, no key.
- [ ] `.venv\Scripts\python.exe -m ytscout analyse --summaries --limit 1` either summarises
      one video (if `claude` is available and a transcript exists) or exits 5 with a
      clear reason. The Outcome says which.
- [ ] `ruff check .` and `ruff format --check .` clean.

## Notes

`DESIGN.md §7` verbatim is the spec. Piped stdin is capped at 10 MB and we do not use it
anyway — the packet goes by path. Keep `video_summary` small: it runs 40× a week.
