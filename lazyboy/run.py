#!/usr/bin/env python
"""Lazyboy driver for YT Scout: one Claude session per issue, verified, then the next.

  python lazyboy/run.py                     # every eligible AFK issue, lowest number first
  python lazyboy/run.py --limit 3           # three issues, then stop
  python lazyboy/run.py 012                 # that issue only (issues/012 works too)
  python lazyboy/run.py --from 020          # skip anything numbered below 020
  python lazyboy/run.py --dry-run           # what would run, and what is blocked
  python lazyboy/run.py 012 --print-prompt  # the brief once.ps1 hands to an interactive claude

Selection is the lowest-numbered issues/NNN-*.md whose **Type** is AFK and whose every
**Blocked by** entry is already in issues/closed/. Blockers may be written as numbers
("003, 004"), as paths, across several lines, or as none/-/em-dash; matching is by the
leading three-digit number. Files in issues/stuck/ are ignored. Active issues are never
picked: they need a person.

**Add dirs** in the header lists directories outside this repo the session may read; each
becomes a --add-dir flag. The pipeline audit uses it to read top-five-animals-1 in place.

Completion is verified, not trusted: the file must be in issues/closed/, HEAD must have
advanced, and the worktree must be clean. A session that cannot finish gets one resumed
retry, then the file is parked in issues/stuck/ with a note and the loop continues.

Nothing is pushed unless --push is given: publishing the branch is the user's call. With
--push the run starts only from a main that matches origin/main.

Per-issue logs land in lazyboy/logs/NNN.jsonl; metrics append to lazyboy/metrics.csv.
Both are gitignored.
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
ISSUES = ROOT / "issues"
CLOSED = ISSUES / "closed"
STUCK = ISSUES / "stuck"
LOGS = ROOT / "lazyboy" / "logs"
METRICS = ROOT / "lazyboy" / "metrics.csv"
PROMPT = ROOT / "lazyboy" / "prompt.md"
SETTINGS = ROOT / "lazyboy" / "settings.json"
NUM = re.compile(r"^(\d{3})-.*\.md$")
REF = re.compile(r"(\d{3})")
NOTHING = ("none", "-", "—", "–", "n/a", "nothing", "nobody")


# --------------------------------------------------------------------------
# reading an issue
# --------------------------------------------------------------------------


def field(text, name):
    """The value of a **Name**: field, including any indented continuation lines."""
    m = re.search(rf"^\*\*{name}\*\*:[ \t]*(.*(?:\n(?![ \t]*(?:\*\*|#|$)).*)*)", text, re.M)
    return " ".join(m.group(1).split()) if m else ""


def blockers(text):
    """The numbers this file waits on, however the field was written."""
    raw = field(text, "Blocked by").strip()
    if not raw or raw.lower() in NOTHING or raw.lower().startswith("none"):
        return []
    return REF.findall(raw)


def add_dirs(text):
    """Directories the issue asks to read, as they were written. Missing ones are reported."""
    raw = field(text, "Add dirs").strip()
    if not raw or raw.lower() in NOTHING:
        return []
    out = []
    for part in re.split(r"[,;]\s*", raw):
        part = part.strip().strip("`").strip()
        if not part:
            continue
        if not Path(part).is_dir():
            print(f"  ! Add dirs: {part} does not exist; the session will not see it.", flush=True)
            continue
        out.append(part)
    return out


def numbers_in(directory):
    return {NUM.match(p.name).group(1) for p in directory.glob("*.md") if NUM.match(p.name)}


def files_in(directory, floor=0):
    return sorted(
        p for p in directory.glob("*.md")
        if NUM.match(p.name) and int(NUM.match(p.name).group(1)) >= floor
    )


# --------------------------------------------------------------------------
# git and claude
# --------------------------------------------------------------------------


def sh(*args, check=True):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=check).stdout.strip()


def run_claude(prompt, log, yolo, resume=None, model=None, extra_dirs=()):
    cmd = [shutil.which("claude") or "claude", "-p", "--verbose", "--output-format", "stream-json",
           "--permission-prompts", "none"]
    if yolo:
        cmd += ["--dangerously-skip-permissions"]
    else:
        cmd += ["--permission-mode", "acceptEdits", "--settings", str(SETTINGS)]
    for d in extra_dirs:
        cmd += ["--add-dir", d]
    if resume:
        cmd += ["--resume", resume]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    result = {}
    with log.open("a", encoding="utf-8") as lf, subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    ) as proc:
        for line in proc.stdout:
            lf.write(line)
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "assistant":
                for c in ev.get("message", {}).get("content", []) or []:
                    if c.get("type") == "text":
                        print(c["text"], flush=True)
                    elif c.get("type") == "tool_use":
                        print(f"  > {c.get('name')}", flush=True)
            elif ev.get("type") == "result":
                result = ev
    return result


def dirty():
    """Paths git reports as modified, added, deleted or untracked."""
    out = []
    for line in sh("git", "status", "--porcelain").splitlines():
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        out.append(path)
    return out


def push_matched(head, push, ok_so_far):
    """Push main when the work passed, and report whether origin caught up."""
    if not (push and ok_so_far):
        return True
    sh("git", "push", "origin", "main", check=False)
    sh("git", "fetch", "-q", "origin", "main", check=False)
    return sh("git", "rev-parse", "origin/main", check=False) == head


def record(unit, attempt, ok, res, started):
    """One row per attempt. The column count is fixed so old rows stay readable."""
    new = not METRICS.exists()
    with METRICS.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["issue", "attempt", "ok", "turns", "duration_s", "cost_usd",
                        "wall_s", "session_id"])
        w.writerow([
            unit, attempt, ok, res.get("num_turns"),
            round((res.get("duration_ms") or 0) / 1000),
            round(res.get("total_cost_usd") or 0, 4),
            round(time.time() - started), res.get("session_id"),
        ])


def park(path, note, push):
    """Move an issue to stuck/ with a note, commit, and carry on."""
    if path.exists():
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n## Lazyboy\n\n{note}\n")
        rel = path.relative_to(ROOT).as_posix()
        sh("git", "mv", rel, f"{STUCK.relative_to(ROOT).as_posix()}/{path.name}", check=False)
        if path.exists():
            shutil.move(path, STUCK / path.name)
    sh("git", "add", "-A", "issues", check=False)
    sh("git", "commit", "-q", "-m", f"lazyboy: park {path.name} as stuck\n\n{note}", check=False)
    if push:
        sh("git", "push", "origin", "main", check=False)
    return True


# --------------------------------------------------------------------------
# issues
# --------------------------------------------------------------------------


def eligible(floor=0, include_active=False):
    """All AFK issues at or above floor, with the blockers not yet closed.

    `include_active` is for once.ps1: a person is in the room, so an Active issue may be
    worked. The unattended loop never passes it.
    """
    done = numbers_in(CLOSED)
    out = []
    for p in files_in(ISSUES, floor):
        text = p.read_text(encoding="utf-8")
        kind = field(text, "Type").upper()
        if kind != "AFK" and not (include_active and kind == "ACTIVE"):
            continue
        out.append((p, [b for b in blockers(text) if b not in done]))
    return out


def next_issue(only=None, floor=0, include_active=False):
    for p, missing in eligible(floor, include_active):
        if only and not p.name.startswith(only):
            continue
        if not missing:
            return p
    return None


def build_prompt(issue: Path):
    commits = sh("git", "log", "-n", "5", "--format=%h %ad %s", "--date=short", check=False)
    return (
        f"# YOUR ISSUE\n\nFile: issues/{issue.name}\n\n{issue.read_text(encoding='utf-8')}\n\n"
        f"# RECENT COMMITS\n\n{commits}\n\n{PROMPT.read_text(encoding='utf-8')}"
    )


def verify(issue: Path, head_before, push):
    moved = (CLOSED / issue.name).exists() and not issue.exists()
    head = sh("git", "rev-parse", "HEAD")
    advanced = head != head_before
    clean = not dirty()
    ok = moved and advanced and clean
    return moved, advanced, clean, push_matched(head, push, ok)


def work(issue: Path, args):
    print(f"\n===== {issue.name} =====", flush=True)
    text = issue.read_text(encoding="utf-8")
    dirs = add_dirs(text)
    head = sh("git", "rev-parse", "HEAD")
    log = LOGS / f"{issue.name[:3]}.jsonl"
    started = time.time()
    res = run_claude(build_prompt(issue), log, args.yolo, model=args.model, extra_dirs=dirs)
    moved, advanced, clean, pushed = verify(issue, head, args.push)
    ok = moved and advanced and clean and pushed
    record(issue.name, 1, ok, res, started)
    if ok:
        print(f"DONE {issue.name}  turns={res.get('num_turns')} "
              f"cost=${res.get('total_cost_usd') or 0:.2f}", flush=True)
        return True

    problem = (f"closed={moved} committed={advanced} clean={clean} pushed={pushed} "
               f"subtype={res.get('subtype')} error={res.get('is_error')}")
    print(f"INCOMPLETE {issue.name}: {problem}", flush=True)
    if res.get("session_id"):
        started = time.time()
        res = run_claude(
            f"The previous attempt ended with: {problem}. Finish the issue: make the feedback "
            f"loops in the brief pass (ruff, pytest, the changed subcommand's --dry-run), commit "
            f"everything so the worktree is clean, append the `## Outcome` note, and "
            f"`git mv issues/{issue.name} issues/closed/`. Do not push. If you truly cannot "
            f"finish it, append a `## Lazyboy` section saying why, commit that, and stop.",
            log, args.yolo, resume=res["session_id"], model=args.model, extra_dirs=dirs,
        )
        moved, advanced, clean, pushed = verify(issue, head, args.push)
        ok = moved and advanced and clean and pushed
        record(issue.name, 2, ok, res, started)
        if ok:
            print(f"DONE {issue.name} on retry", flush=True)
            return True

    note = f"Parked after two attempts: {problem}. Last result: {(res.get('result') or '')[:800]}"
    print(f"STUCK {issue.name}", flush=True)
    if dirty():
        sh("git", "add", "-A", check=False)
        sh("git", "commit", "-q", "-m", f"lazyboy: WIP on {issue.name} (incomplete)", check=False)
    return park(issue, note, args.push)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_target(raw):
    """`issues/012`, `012` or `issues` -> an optional three-digit number."""
    text = (raw or "issues").replace("\\", "/").strip().strip("/")
    kind, _, rest = text.partition("/")
    if kind.isdigit() and not rest:
        return f"{int(kind):03d}"
    if kind.lower() != "issues":
        sys.exit(f"Unknown target {raw!r}: expected `issues`, `issues/012` or `012`.")
    if not rest:
        return None
    match = re.match(r"(\d{1,3})", rest.rsplit("/", 1)[-1])
    if not match:
        sys.exit(f"Cannot read an issue number out of {raw!r}; try `012`.")
    return f"{int(match.group(1)):03d}"


def dry_run(floor):
    rows = eligible(floor)
    if not rows:
        print("No AFK issues at or above the floor. Active issues wait for you.")
    for p, missing in rows:
        print(("WAITS  " if missing else "READY  ") + p.name
              + (f"  <- {', '.join(missing)}" if missing else ""))
    active = [p.name for p in files_in(ISSUES, floor)
              if field(p.read_text(encoding="utf-8"), "Type").upper() == "ACTIVE"]
    if active:
        print("\nActive (needs you):")
        for name in active:
            print(f"  {name}")


def preflight(args):
    if not shutil.which("claude"):
        sys.exit("`claude` is not on PATH. Install Claude Code and log in with your subscription.")
    if sh("git", "rev-parse", "--verify", "-q", "HEAD", check=False) == "":
        sys.exit("No commits yet. Make an initial commit before running lazyboy.")
    if dirty():
        sys.exit("Worktree is dirty; commit or stash before running lazyboy.")
    if sh("git", "rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("Lazyboy commits to main; check out main before running it.")
    if args.push:
        sh("git", "fetch", "-q", "origin", "main", check=False)
        if sh("git", "rev-parse", "HEAD") != sh("git", "rev-parse", "origin/main", check=False):
            sys.exit("--push needs main and origin/main to match; pull or push first.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default="issues",
                    help="issues (default), issues/012, or just 012")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many sessions")
    ap.add_argument("--from", dest="floor", type=int, default=0,
                    help="skip anything numbered below this, e.g. --from 020")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-prompt", action="store_true",
                    help="print the prompt for the next issue and exit; once.ps1 pipes "
                         "this into an interactive claude so the brief is built in one place")
    ap.add_argument("--include-active", action="store_true",
                    help="with --print-prompt: allow an Active issue (a person is present)")
    ap.add_argument("--push", action="store_true",
                    help="push main to origin after each session (off by default)")
    # --yolo drops the allowlist but not lazyboy/guard.py: PreToolUse hooks still run
    # under --dangerously-skip-permissions, so the guard's rules hold either way.
    ap.add_argument("--yolo", action="store_true")
    ap.add_argument("--model")
    args = ap.parse_args()
    only = parse_target(args.target)
    for d in (CLOSED, STUCK, LOGS):
        d.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        dry_run(args.floor)
        return

    if args.print_prompt:
        issue = next_issue(only, args.floor, include_active=args.include_active)
        if issue is None:
            what = "issue" if args.include_active else "AFK issue"
            sys.exit(f"lazyboy: no eligible {what} matching {only or 'anything'} "
                     f"(closed, blocked, or missing).")
        print(build_prompt(issue))
        return

    preflight(args)

    n = 0
    while True:
        issue = next_issue(only, args.floor)
        if issue is None:
            if only:
                print(f"lazyboy: {only} is not eligible (Active, blocked, closed or missing).")
            elif args.floor:
                print(f"lazyboy: no eligible AFK issue left at or above {args.floor:03d}.")
            else:
                print("lazyboy: no eligible AFK issue left.")
            break
        if not work(issue, args):
            break
        n += 1
        # One issue is one session, so a single target has nothing left to do.
        if only or (args.limit and n >= args.limit):
            break

    blocked = [p.name for p, missing in eligible(args.floor) if missing]
    if blocked:
        print(f"{len(blocked)} issue(s) still blocked (see --dry-run).")


if __name__ == "__main__":
    main()
