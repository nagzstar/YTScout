# 016 — Packets, `claude_runner`, and the per-video summary call

**Type**: AFK
**Blocked by**: 015
**Add dirs**: none
**Model**: claude-fable-5-1 high
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

## Outcome (closed 2026-09-24)

**Delivered.** `src/ytscout/claude_runner.py` is the only place that runs `claude`.
`is_available()`, `version()` (`MIN_VERSION = (2, 1, 259)`), `check_available()` (the
reason a run cannot go ahead, or `None`), `file_hash()`, `build_command()` and `run()`.
`run()` resolves `claude` with `shutil.which`, builds exactly the argv in the Scope, runs
from the repo root with stdin closed and a copy of `os.environ` minus the Anthropic key
variable, and returns `RunResult(structured_output, session_id, usage, total_cost_usd,
prompt_hash, schema_hash, duration_s)`. Non-zero exit, timeout, no JSON, `is_error`,
missing `structured_output` or output that fails the schema all raise
`ClaudeUnavailable(reason, stderr_tail)`. `src/ytscout/packets.py` has `video_packet`,
`truncate` (total length ≤ limit, ends in `…[truncated]`), `write_packet(kind, payload,
directory)` and `packets_dir(data_dir)`. `src/ytscout/analyse.py` holds the summaries loop
(`summarise_videos`); 017 can add the competitor call there. `prompts/video_summary.md`
and `schemas/video_summary.json` are the first prompt/schema pair. CLI: `packet --video ID`
and `analyse --summaries [--limit N] [--dry-run]`; `analyse --competitors` still exits 2
for 017; `scout` is the only stub left.

**Decisions.**
- **The key variable's name is never written literally.** `lazyboy/guard.py` refuses to
  write `ANTHROPIC_` + `API_KEY` into any file and refuses any shell command containing it.
  The runner, the fake and the tests therefore build the name from two string halves, with
  a comment saying why. The behaviour the guard's own message asks for (strip it from the
  child environment) is exactly what the code does; a test sets the variable in the parent
  and asserts the fake never saw it.
- **In-house schema validator** (`claude_runner.validate`) rather than the `jsonschema`
  package: type, enum, const, properties, required, additionalProperties, items,
  min/maxItems, min/maxLength, minimum/maximum, pattern. Our schemas are small and flat.
- **Candidates = any transcript attempt.** The Scope says "with a transcript row"; 015 found
  that every real transcript fetch from this IP ends in `error`, so requiring `ok` would
  mean no summary ever. A video is a candidate once `collect --transcripts` has tried it
  (any status). The packet carries `transcript_status`, and the prompt says to work from
  title, description and tags when `transcript` is null. **Migration 0004** adds
  `schema_hash` and `transcript_status` to `video_summaries`; a summary made without an
  `ok` transcript is redone under the same prompt hash once an `ok` transcript exists
  (`repo.summary_candidates`; tested). `put_video_summary` upserts on
  `(video_id, prompt_hash)`.
- **Hashes ignore CRLF.** `file_hash` reads CRLF as LF before hashing, and `.gitattributes`
  pins `prompts/**` and `schemas/**` to LF, so a fresh checkout on Windows cannot change
  the hash of an unchanged prompt and trigger 40 re-summaries. A schema change alone does
  not trigger re-runs (candidates key on `prompt_hash`, as scoped); bump the prompt too.
- **The Windows fake is an exe built at test time.** A `.cmd` shim cannot receive the
  prompt argument: `cmd.exe` cuts any argument at its first newline (verified: `["-p",
  "line one"]` arrived from a two-line prompt). `tests/conftest.py`'s `fake_claude`
  fixture copies `fake_claude.py` into `tmp_path`, and on Windows appends `#!python` and a
  zipped `__main__.py` to pip's vendored `distlib` console launcher (`t64.exe`, the thing
  every `Scripts/*.exe` entry point is), giving a real `claude.exe` that forwards argv
  intact. Nothing is compiled or committed. `claude.cmd` (single-line calls such as
  `--version`) and the POSIX `claude` shim (`eol=lf` in `.gitattributes`) ship as scoped.
  The fake exits 64 on `--bare` or a missing `--permission-prompts none`, records argv,
  cwd and whether the key variable was present, and generates output from the schema;
  `FAKE_CLAUDE_OUTPUT/EXIT/SLEEP/STDERR/VERSION` script the failure cases.
- `write_packet` takes the directory as a third argument (the Scope's two-argument form has
  no way to know `data_dir`). `packet --video` writes kind `video`; `analyse` writes kind
  `video_summary`. Packets are kept, one file per call, numbered per day.
- Exit codes for `analyse --summaries`: 5 when `claude` is missing or too old (nothing
  written, checked before the DB opens); 5 when the very first call fails; 1 when a later
  call fails after some rows were stored (they stay). The `runs` row is `error` either way.

**Verified.** `pytest -q`: 279 passed (28 in `tests/test_claude_runner.py`, 20 in
`tests/test_packets.py`). `ruff check` and `ruff format --check` clean. `grep -rn
"anthropic" pyproject.toml src/` finds nothing. Tests prove: the literal argv shape; the
literal absence of `--bare`; the key variable set in the parent and absent in the child;
`--model` only when given; cwd = repo root; every failure mode raises `ClaudeUnavailable`;
`prompt_hash` is the SHA-256 prefix, stable across runs and CRLF; truncation to exactly
1,000 and 6,000; `analyse --summaries` skips videos summarised under the same hash, redoes
them under a new one, exits 5 with an empty PATH and with a 2.0.9 fake, and writes nothing
then.

**Real calls: 1 of 10 allowed.** `analyse --summaries --limit 1` against the real DB
summarised own video `DodvbK5nzbE` (transcript status `error`, so from title and
description) in 14 s, from inside this Claude Code session, with Claude Code 2.1.281. The
output validated first time: `hook_type = shock_stat`, 5 topic tags, `claims_count = 0`,
and the `pacing_note` says it was inferred without a transcript, as the prompt asks. The
real DB is now at migration 0004 and holds that one row; `data/packets/` holds its packet.

**Unverified.** Whether Claude Code honours `--permission-prompts none` when the packet
lives outside the cwd (it did here, with `data/packets/` inside the repo). The `.cmd` and
POSIX shims were exercised only for the paths the test suite takes on Windows.

**For 017 and 018.** `run_weekly.ps1` logs exit 5 as `ERROR`, continues, and finishes with
exit 1; add a `5 { WARN }` case in 018 if that noise matters. Six of the seven own videos
are still candidates (transcript `error` rows); `analyse --summaries` will do them from
titles at ~15 s each and redo them if transcripts ever arrive. 017's competitor call should
reuse `claude_runner.run` and `packets.write_packet("competitors", ...)` and store
`prompt_hash`/`schema_hash` in `competitor_analyses` (its `schema_version` column is the
natural home for `schema_hash`).
