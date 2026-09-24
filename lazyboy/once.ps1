# Interactive run of one issue - you answer any prompts, and the guard still applies.
# The prompt is built by run.py, so it is the same brief the unattended loop gives.
# This is how Active issues get worked: you in the room, Claude doing the typing.
#
# Usage: .\lazyboy\once.ps1 012          that issue
#        .\lazyboy\once.ps1 issues/012   the same
param([Parameter(Mandatory = $true)][string]$Target)

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Push-Location $repo
try {
    $prompt = & $python "lazyboy\run.py" $Target --print-prompt --include-active | Out-String
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    claude --permission-mode acceptEdits --settings lazyboy\settings.json $prompt
} finally { Pop-Location }
