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
