# Unattended loop: one Claude session per issue, verified, then the next.
# Usage: .\lazyboy\afk.ps1 [target] [--limit N] [--from NNN] [--push] [--yolo] [--model m] [--dry-run]
#   .\lazyboy\afk.ps1                 every eligible AFK issue, lowest number first
#   .\lazyboy\afk.ps1 012             that issue only
#   .\lazyboy\afk.ps1 --limit 2       two sessions, then stop
#   .\lazyboy\afk.ps1 --dry-run       what would run, what is blocked, what needs you
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Push-Location $repo
try { & $python "lazyboy\run.py" @args } finally { Pop-Location }
