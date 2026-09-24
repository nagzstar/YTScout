<#
.SYNOPSIS
    Register (or replace) the "YTScout Weekly" Windows Task Scheduler job.

.DESCRIPTION
    Runs scripts\run_weekly.ps1 weekly as the current user, waking the PC if it is
    asleep and starting late if it was off at the scheduled time. Re-running this
    script replaces the task (-Force). Registering the task is an Active step (issue 014).

    The default Monday 03:00 is deliberate: see the comment at the top of run_weekly.ps1
    before changing it.

    Windows PowerShell 5.1-compatible.

.PARAMETER At
    Time of day, HH:mm (default 03:00).

.PARAMETER Day
    Day of the week (default Monday).
#>
[CmdletBinding()]
param(
    [string]$At = '03:00',
    [System.DayOfWeek]$Day = [System.DayOfWeek]::Monday
)

$ErrorActionPreference = 'Stop'

$TaskName = 'YTScout Weekly'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $RepoRoot 'scripts\run_weekly.ps1'

if (-not (Test-Path $Script)) {
    throw "run_weekly.ps1 not found at $Script"
}

$time = [datetime]::ParseExact($At, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $time
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Output "Registered '$TaskName': every $Day at $At as $user."
Write-Output "Runs: powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Script`""
Write-Output ''
Write-Output 'Inspect it:'
Write-Output "  Get-ScheduledTask '$TaskName' | Format-List *"
Write-Output "  Get-ScheduledTaskInfo '$TaskName'    # last run time and result"
Write-Output 'Run it once by hand:'
Write-Output "  Start-ScheduledTask '$TaskName'"
Write-Output "Logs land in $RepoRoot\logs\weekly-YYYYMMDD-HHMM.log"
