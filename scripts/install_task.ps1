<#
.SYNOPSIS
    Register (or replace) the "YTScout Weekly" Windows Task Scheduler job.

.DESCRIPTION
    Runs scripts\run_weekly.ps1 weekly as the current user, waking the PC if it is
    asleep and starting late if it was off at the scheduled time. Re-running this
    script replaces the task (-Force). Registering the task is an Active step (issue 014).

    The default Monday 03:00 is deliberate: see the comment at the top of run_weekly.ps1
    before changing it.

    A second trigger, Tuesday 09:00 (after the 08:00 UK quota reset), re-runs the same
    task so a quota stop costs one day, not the week (issue 032). Task Scheduler puts
    arguments on the action, not the trigger, so both triggers run
    `run_weekly.ps1 -Resume`. On Monday that changes nothing: checkpoints are keyed by
    ISO week and the Monday run is the week's first. On Tuesday the collectors skip what
    Monday finished (no API calls for a completed week); the analyse, score and dashboard
    steps re-run, and they spend no Data API quota.

    Windows PowerShell 5.1-compatible.

.PARAMETER At
    Time of day, HH:mm (default 03:00).

.PARAMETER Day
    Day of the week (default Monday).

.PARAMETER ResumeAt
    Time of day, HH:mm, of the resume trigger (default 09:00).

.PARAMETER ResumeDay
    Day of the week of the resume trigger (default Tuesday).
#>
[CmdletBinding()]
param(
    [string]$At = '03:00',
    [System.DayOfWeek]$Day = [System.DayOfWeek]::Monday,
    [string]$ResumeAt = '09:00',
    [System.DayOfWeek]$ResumeDay = [System.DayOfWeek]::Tuesday
)

$ErrorActionPreference = 'Stop'

$TaskName = 'YTScout Weekly'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $RepoRoot 'scripts\run_weekly.ps1'

if (-not (Test-Path $Script)) {
    throw "run_weekly.ps1 not found at $Script"
}

$culture = [System.Globalization.CultureInfo]::InvariantCulture
$time = [datetime]::ParseExact($At, 'HH:mm', $culture)
$resumeTime = [datetime]::ParseExact($ResumeAt, 'HH:mm', $culture)

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Resume" `
    -WorkingDirectory $RepoRoot
$trigger = @(
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $time),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $ResumeDay -At $resumeTime)
)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Output "Registered '$TaskName': every $Day at $At, and every $ResumeDay at $ResumeAt to resume a quota-stopped week, as $user."
Write-Output "Runs: powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Resume"
Write-Output ''
Write-Output 'Inspect it:'
Write-Output "  Get-ScheduledTask '$TaskName' | Format-List *"
Write-Output "  Get-ScheduledTaskInfo '$TaskName'    # last run time and result"
Write-Output 'Run it once by hand:'
Write-Output "  Start-ScheduledTask '$TaskName'"
Write-Output "Logs land in $RepoRoot\logs\weekly-YYYYMMDD-HHMM.log"
