#!/usr/bin/env python
"""PreToolUse hook for unattended Lazyboy runs on the YT Scout repo.

On **Bash**, the permission allowlist in lazyboy/settings.json is prefix-matched, so it
cannot tell `ytscout collect --dry-run` from `ytscout collect`, and a compound command
hides a denied prefix behind an allowed one. This hook reads the whole command string and
blocks the things that would make an unattended run dangerous rather than merely wrong:

  1. Burning the YouTube Data API quota the weekly job needs: a collector run with no
     `--max-units`, or one above the per-session ceiling.
  2. Getting YouTube data any way other than the official API: scraper libraries,
     direct fetches of youtube.com, extra Cloud projects. Nagz owns a monetised channel
     and the ToS risk is his, not the session's to take.
  3. Talking to Claude any way other than the subscription: setting ANTHROPIC_API_KEY,
     `claude --bare`, or a nested `--dangerously-skip-permissions`.
  4. Reading a secret into the transcript, a file or a commit.
  5. Acts that need a person in the room: the OAuth consent flow, registering the Task
     Scheduler job, the review server.
  6. Pushes, force pushes, history rewrites, hard resets and recursive deletes.

On **Edit, Write and MultiEdit** it protects the user's own config and secrets from being
written by a session, and stops an API key from being written into any file.

Exit 2 blocks the call and returns the message on stderr to the model, which then has to
find another way. Everything else passes straight through.
"""
import json
import re
import sys

MAX_UNITS_PER_SESSION = 3000

RULES = [
    # --- quota -------------------------------------------------------------------------
    (
        re.compile(r"\bytscout\s+(collect|discover)\b(?![^;&|]*--(dry-run|help|max-units))"),
        f"That hits the YouTube Data API with no cap. Every real collector run in a session "
        f"passes --max-units N (N <= {MAX_UNITS_PER_SESSION}, and no more than the issue's "
        f"Scope grants); use --dry-run to see the plan. The weekly job needs the rest of "
        f"the 10,000.",
    ),
    (
        re.compile(r"\bytscout\s+scout\s+(validate|snowball)\b(?![^;&|]*--(dry-run|help|max-units))"),
        f"That runs search.list (100 units each) with no cap. Pass --max-units N "
        f"(N <= {MAX_UNITS_PER_SESSION}) or --dry-run.",
    ),
    (
        re.compile(r"--max-units[= ]\s*(\d+)"),
        None,  # handled specially below: the number must be within the ceiling
    ),
    # --- no scraping -------------------------------------------------------------------
    (
        re.compile(r"\b(pip|pip3|uv|poetry|pipx)\b[^;&|]*\b(yt-dlp|yt_dlp|pytube|pytubefix|"
                   r"scrapetube|youtube-search-python|selenium|playwright|undetected[-_]chromedriver|"
                   r"seleniumbase|pyppeteer)\b"),
        "That is a scraper or a browser driver. YT Scout reads YouTube only through the "
        "official Data API and Analytics API (DESIGN.md decision 3). The one exception "
        "already in pyproject is youtube-transcript-api; nothing else gets added.",
    ),
    (
        re.compile(r"\b(curl|wget|iwr|Invoke-WebRequest|Invoke-RestMethod|httpx|http\.get|"
                   r"requests\.get|urlopen)\b[^;&|]*(youtube\.com|youtu\.be|googlevideo\.com)"),
        "Do not fetch youtube.com directly. Public data comes from the Data API client in "
        "src/ytscout/youtube/, transcripts from youtube-transcript-api, nothing else.",
    ),
    (
        re.compile(r"\bgcloud\s+projects\s+create\b|\bgcloud\s+services\s+enable\b"),
        "One Google Cloud project, created by the user. Creating projects or enabling APIs "
        "is not a session's job, and a second project to stretch quota breaks the API terms.",
    ),
    # --- claude via subscription only --------------------------------------------------
    (
        re.compile(r"ANTHROPIC_API_KEY"),
        "This project never uses an Anthropic API key. Claude is called through the "
        "subscription with `claude -p`; claude_runner.py strips ANTHROPIC_API_KEY from the "
        "environment on purpose. Do not set, read or reference it.",
    ),
    (
        re.compile(r"\bclaude\b[^;&|]*\s--bare\b"),
        "`--bare` skips the subscription login and needs an API key, which this project "
        "does not have. Call `claude -p` without it.",
    ),
    (
        re.compile(r"\bclaude\b[^;&|]*--dangerously-skip-permissions"),
        "A nested claude with permissions off is out of scope for a session. Use `claude -p` "
        "with --allowedTools Read --permission-prompts none, as claude_runner.py does.",
    ),
    # --- secrets -----------------------------------------------------------------------
    (
        re.compile(r"\b(cat|type|less|more|head|tail|bat|grep|rg|sed|awk|printf|echo|"
                   r"Get-Content|gc|python(?:\.exe)?\s+-c)\b[^;&|]*"
                   r"(client_secret[^\s\"']*\.json|(?<![\w.])token\.json|"
                   r"scripts[/\\]\.secrets|(?<![\w.])\.env(?!\.example)(?![\w]))"),
        "Do not read a secret into the transcript. `ytscout doctor` reports which secrets "
        "are present without printing them; refer to them by file name.",
    ),
    (
        re.compile(r"\b(cp|copy|mv|move|Copy-Item|Move-Item|cpi|mi|curl|Invoke-WebRequest|iwr|"
                   r"Invoke-RestMethod|irm|scp|rsync|zip|tar|7z|Compress-Archive)\b[^;&|]*"
                   r"(scripts[/\\]\.secrets|(?<![\w.])token\.json|client_secret[^\s\"']*\.json|"
                   r"(?<![\w.])\.env(?!\.example)(?![\w]))"),
        ".env and scripts/.secrets hold the API key, the OAuth client secret and the OAuth "
        "token. Code reads them through ytscout.settings; a session does not copy, move or "
        "send those files.",
    ),
    # --- acts that need a person -------------------------------------------------------
    (
        re.compile(r"\bytscout\s+auth\b(?![^;&|]*--(help|status|dry-run))"),
        "`ytscout auth` opens a Google consent screen and nobody is here to click it. That "
        "issue is Active. Test the OAuth code with a fake credentials object.",
    ),
    (
        re.compile(r"\bytscout\s+serve\b"),
        "`ytscout serve` is the review server for a person to click approve/reject in; it "
        "would sit waiting forever. Test it in-process on an ephemeral port instead.",
    ),
    (
        re.compile(r"\b(schtasks|Register-ScheduledTask|Unregister-ScheduledTask|"
                   r"Set-ScheduledTask|Start-ScheduledTask|install_task\.ps1)\b"),
        "Installing or running the Task Scheduler job is the user's call (an Active issue). "
        "Write scripts/install_task.ps1, prove run_weekly.ps1 with -DryRun, and stop there.",
    ),
    # --- git -----------------------------------------------------------------------------
    (
        re.compile(r"\bgit\s+push\b"),
        "You do not push. The runner pushes when the user started it with --push. "
        "Commit locally and let it decide.",
    ),
    (
        re.compile(r"\bgit\s+(push\b[^;&|]*(--force|--force-with-lease|(?<!\w)-f(?!\w)|--delete|--mirror)"
                   r"|rebase(?!\s+--abort)|reset\s+--hard|filter-branch|reflog\s+delete|checkout\s+--\s)"),
        "That rewrites or discards work the runner cannot recover. Move forward with new "
        "commits instead; `git rebase --abort` is the one exception.",
    ),
    (
        re.compile(r"\bgh\s+(workflow\s+run|secret|release|api|repo\s+(delete|edit|create))\b"),
        "The GitHub CLI is out of scope for an unattended run: issues here are local "
        "markdown, and secrets, releases and repo settings are the user's.",
    ),
    (
        re.compile(r"\brm\s+-rf\b|\bgit\s+clean\s+-[a-z]*f|\bRemove-Item\b[^;&|]*-Recurse"),
        "A recursive delete in an unattended run is how a session loses data/ytscout.sqlite. "
        "Remove named files if you must, nothing wider.",
    ),
]

# Files a session may not write. settings.yaml is the user's; the rest are secrets or
# the database, which is written through the store, never by hand.
PROTECTED_WRITE = re.compile(
    r"(?:^|[/\\])(config[/\\]settings\.yaml|\.env|\.secrets[/\\][^/\\]+|client_secret[^/\\]*\.json|"
    r"token\.json|[^/\\]+\.sqlite(?:-wal|-shm|-journal)?)$", re.I)
SECRET_IN_CONTENT = re.compile(r"ANTHROPIC_API_KEY|AIza[0-9A-Za-z_\-]{30,}|sk-ant-[0-9A-Za-z_\-]{20,}")

PROTECTED_WRITE_MESSAGE = (
    "That file is the user's config, a secret, or the database. Sessions change "
    "config/settings.example.yaml (never settings.yaml), keep secrets out of the repo, and "
    "write to SQLite only through ytscout.store."
)
SECRET_IN_CONTENT_MESSAGE = (
    "That content contains an API key or a reference to ANTHROPIC_API_KEY. Nothing in this "
    "repo holds a key: the YouTube key lives in .env (gitignored) and Claude is called "
    "through the subscription."
)


def block(message):
    print(f"Blocked by lazyboy/guard.py: {message}", file=sys.stderr)
    sys.exit(2)


def written_text(tool_input):
    """Every string an Edit, Write or MultiEdit would put into the file."""
    parts = [tool_input.get("content") or "", tool_input.get("new_string") or ""]
    for edit in tool_input.get("edits") or []:
        if isinstance(edit, dict):
            parts.append(edit.get("new_string") or "")
    return "\n".join(parts)


def guard_write(tool_input):
    path = (tool_input.get("file_path") or "").replace("\\", "/")
    if path and PROTECTED_WRITE.search(path):
        block(PROTECTED_WRITE_MESSAGE)
    if SECRET_IN_CONTENT.search(written_text(tool_input)):
        block(SECRET_IN_CONTENT_MESSAGE)


def guard_bash(cmd):
    for pattern, message in RULES:
        m = pattern.search(cmd)
        if not m:
            continue
        if message is None:  # the --max-units ceiling
            for units in re.findall(r"--max-units[= ]\s*(\d+)", cmd):
                if int(units) > MAX_UNITS_PER_SESSION:
                    block(
                        f"--max-units {units} is above the per-session ceiling of "
                        f"{MAX_UNITS_PER_SESSION}. Bigger runs are the user's, from the "
                        f"weekly job or by hand."
                    )
            continue
        block(message)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        guard_write(tool_input)
    elif tool_input.get("command"):
        guard_bash(tool_input["command"])

    sys.exit(0)


if __name__ == "__main__":
    main()
