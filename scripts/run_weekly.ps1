<#
.SYNOPSIS
    YT Scout weekly run: collect, analyse, score, rebuild the dashboard.

.DESCRIPTION
    Scheduled for Monday 03:00 UK by install_task.ps1. 03:00 UK is *before* the
    Pacific-midnight quota reset (08:00 UK), so the run spends Sunday's Pacific quota,
    which is fine as long as nothing else spent it. Do not move the time without
    re-reading DESIGN.md section 8 (quota budget).

    Exit codes from each step:
      0  ok
      2  not implemented yet -> logged, continue
      3  quota exhausted     -> logged, skip the remaining collectors, still score and
                                rebuild the dashboard, exit 3 at the end
      4  no OAuth token      -> on `collect --analytics` only: logged warning, continue
      other non-zero         -> logged, continue, exit 1 at the end
    The dashboard is always rebuilt last so a partial week is still visible.

    Windows PowerShell 5.1-compatible: no `??`, no ternary.

.PARAMETER DryRun
    Print each command instead of running it, write no log, exit 0.

.PARAMETER Resume
    Pass --resume to the collectors (issue 032; a no-op until then).

.PARAMETER Python
    Interpreter to run ytscout with (default: the repo venv). A test seam.

.PARAMETER LogDir
    Where the weekly-YYYYMMDD-HHMM.log goes (default: <repo>\logs). A test seam.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Resume,
    [string]$Python = '',
    [string]$LogDir = ''
)

$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
}
$MaxUnits = '8000'

$EXIT_NOT_IMPLEMENTED = 2
$EXIT_QUOTA = 3
$EXIT_NO_TOKEN = 4

# Each step: its ytscout arguments and whether it is a collector (quota-bound).
$Steps = @(
    @{ Args = @('collect', '--own', '--max-units', $MaxUnits); Collector = $true },
    @{ Args = @('collect', '--competitors', '--max-units', $MaxUnits); Collector = $true },
    @{ Args = @('collect', '--analytics', '--max-units', $MaxUnits); Collector = $true },
    @{ Args = @('collect', '--transcripts', '--max-units', $MaxUnits); Collector = $true },
    @{ Args = @('collect', '--niches', '--max-units', $MaxUnits); Collector = $true },
    @{ Args = @('analyse', '--summaries'); Collector = $false },
    @{ Args = @('analyse', '--competitors'); Collector = $false },
    @{ Args = @('score', '--competitors'); Collector = $false },
    @{ Args = @('dashboard'); Collector = $false }
)

function Get-StepArgs($Step) {
    $a = @('-m', 'ytscout') + $Step.Args
    if ($Resume -and $Step.Collector) {
        $a += '--resume'
    }
    return , $a
}

if ($DryRun) {
    foreach ($step in $Steps) {
        $a = Get-StepArgs $step
        Write-Output ('.venv\Scripts\python.exe ' + ($a -join ' '))
    }
    exit 0
}

if (-not $LogDir) {
    $LogDir = Join-Path $RepoRoot 'logs'
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$LogFile = Join-Path $LogDir ('weekly-' + (Get-Date -Format 'yyyyMMdd-HHmm') + '.log')

function Write-Log([string]$Message) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Output $line
}

Write-Log "YT Scout weekly run starting in $RepoRoot"
if (-not (Test-Path $Python)) {
    Write-Log "ERROR: $Python not found; create the venv first (see README)."
    exit 1
}

$env:PYTHONIOENCODING = 'utf-8'
$quotaHit = $false
$failed = $false

Push-Location $RepoRoot
try {
    foreach ($step in $Steps) {
        $a = Get-StepArgs $step
        $cmd = 'python ' + ($a -join ' ')
        if ($quotaHit -and $step.Collector) {
            Write-Log "SKIP  $cmd  (quota exhausted earlier this run)"
            continue
        }
        Write-Log "RUN   $cmd"
        & $Python @a 2>&1 | ForEach-Object { Write-Log ('      ' + "$_") }
        $code = $LASTEXITCODE
        Write-Log "EXIT  $code  $cmd"

        if ($code -eq 0) {
            continue
        }
        elseif ($code -eq $EXIT_NOT_IMPLEMENTED) {
            Write-Log "NOTE  exit 2: not implemented yet (or unknown flag); continuing"
        }
        elseif ($code -eq $EXIT_QUOTA) {
            Write-Log "WARN  exit 3: quota exhausted; skipping the remaining collectors"
            $quotaHit = $true
        }
        elseif ($code -eq $EXIT_NO_TOKEN -and ($step.Args -contains '--analytics')) {
            Write-Log "WARN  exit 4: no OAuth token; run 'python -m ytscout auth' once. Skipping analytics"
        }
        else {
            Write-Log "ERROR exit $code; continuing"
            $failed = $true
        }
    }
}
finally {
    Pop-Location
}

if ($quotaHit) {
    Write-Log "DONE  exit 3 (quota exhausted). Log: $LogFile"
    exit 3
}
if ($failed) {
    Write-Log "DONE  exit 1 (a step failed). Log: $LogFile"
    exit 1
}
Write-Log "DONE  exit 0. Log: $LogFile"
exit 0
