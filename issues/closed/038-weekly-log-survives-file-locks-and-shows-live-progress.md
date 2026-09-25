# 038 — The weekly log survives file locks and shows live progress

**Type**: AFK
**Blocked by**: 014
**Add dirs**: none
**Model**: claude-opus-5-5 medium
**Covers**: scripts/run_weekly.ps1, tests/test_scripts.py
**Milestone**: M5

## Why

In the first real weekly run (014) every `Add-Content` in `run_weekly.ps1` failed with
"the process cannot access the file ... because it is being used by another process" from
01:04 to 01:50, because a `tail -f` on the log was open in another window. The script
kept running but the log lost 77 lines: the whole transcripts step, the niches step and
the `RUN` line for summaries. Anything that reads the log while the job runs (an editor,
a viewer, 031's usage panel) can do the same. Also, Python buffers stdout when piped, so
a 30-minute `collect --transcripts` wrote nothing to the log until it exited: the log
cannot show where a run is.

## Scope

- Replace `Add-Content` in `Write-Log` with a writer that opens the file with
  `FileShare.ReadWrite` (a `[System.IO.StreamWriter]` over a `FileStream` opened
  `Append, Write, ReadWrite`), or retries `Add-Content` up to 5 times with 200 ms sleeps
  and falls back to stdout. Either way no line is lost when a reader holds the file open.
- Run every step with `python -u` (or set `PYTHONUNBUFFERED=1` next to
  `PYTHONIOENCODING`) so each per-video line reaches the log as it happens.
- A test in `tests/test_scripts.py` that holds the log open for reading with a competing
  handle (`[System.IO.File]::Open(..., 'Open', 'Read', 'None')` in a background job, or
  Python's `open()` with `msvcrt.locking`) while the script writes, and asserts no line is
  missing. If that is not reliably reproducible on Windows, the test at least asserts the
  `-u` flag is in the dry-run output and the writer is the shared-mode one.

## Out of scope

- Log rotation, the usage panel (031), tailing tooling.

## Acceptance criteria

- [ ] `run_weekly.ps1 -DryRun` prints `python -u -m ytscout ...` (or the env var is set
      and tested).
- [ ] The lock test passes, or the Outcome says why it could not be written and what was
      proved instead.
- [ ] `pytest -q` passes.

## Notes

The lost run is `logs/weekly-20260925-0104.log`; lines 20–96 were restored by hand from
the console and 97–105 reconstructed from the `runs` table, each block under a NOTE marker. The 014 Outcome has the timings.

## Outcome (closed 2026-09-25)

Delivered in `scripts/run_weekly.ps1`:

- `Write-Log` no longer uses `Add-Content`. Each line is appended through a
  `[System.IO.StreamWriter]` over a `FileStream` opened `Append, Write, ReadWrite`
  (UTF-8, no BOM), so a reader that shares read/write (`tail -f`, an editor, 031's
  panel) no longer blocks the writer.
- A reader that *denies* writers cannot be defeated by any share mode, so lines that
  cannot be written go into a pending buffer: 5 tries × 200 ms for a fresh line, then
  one try per line while a backlog exists (a 45-minute lock would otherwise add 1 s to
  every per-video line), and the whole backlog is written, in order, at the next
  success. `Complete-Log` gives the final `DONE` line up to 5 s more. Every line
  still goes to stdout too.
- Every step runs `python -u -m ytscout ...` and `PYTHONUNBUFFERED=1` is set next to
  `PYTHONIOENCODING`. `-DryRun` prints `.venv\Scripts\python.exe -u -m ytscout ...`.
  Log lines now read `RUN   python -u -m ytscout ...`.

Proved by `tests/test_scripts.py` (all run, `pytest -q` 535 passed):

- `test_dry_run_runs_python_unbuffered`: `-u` on every dry-run line, env var in the script.
- `test_log_survives_a_shared_reader_like_tail` and `test_log_survives_an_exclusive_reader`:
  a real (non-dry) run against a fake interpreter (a `.cmd` wrapping a Python helper).
  On the transcripts step the helper opens `YTSCOUT_RUN_LOG` with `CreateFileW`
  (read access; share read|write for 1 s, then share none for 3 s) while printing lines.
  The tests assert the lock was taken, and that the log equals the script's stdout line
  for line, in order, and ends in `DONE  exit 0`.
- The exit-code tests' fake `.cmd` matches one positional argument later because of `-u`.

Left: a lock held past the end of the run (more than 5 s after `DONE`) still loses the
tail of the log file; stdout has it. Not checked: a real `tail -f` from Git Bash against a
scheduled run. The share-read/write test is the stand-in for it.
