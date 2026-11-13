<#
.SYNOPSIS
    Keeps prices and the daily snapshot up to date even on days you don't open the dashboard.

.DESCRIPTION
    Registers a Windows scheduled task (for your user account only, no admin rights needed) that
    runs "app.py --update" in the background, with no window:
      * every day at the chosen time, or as soon as the PC is next on if it was off then;
      * a few minutes after you sign in to Windows.
    Each run checks first and does nothing if today's snapshot is already saved and prices are
    fresh, or if the dashboard is open (it updates itself). If the PC is off all day, that day
    is simply skipped: charts carry the last value across the gap.
    What it did is written to data\update.log.

        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1
        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1 -Time 18:30
        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1 -Remove

.PARAMETER Time
    Time of day for the daily run (24-hour clock, default 12:00).

.PARAMETER Remove
    Delete the scheduled task again.
#>
param([string]$Time = '12:00', [switch]$Remove)

$ErrorActionPreference = 'Stop'
$taskName = 'CS2 Inventory Intelligence - daily update'
$root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed the scheduled task '$taskName'."
    } else {
        Write-Host "There is no scheduled task called '$taskName'."
    }
    return
}

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "Python environment not found. Run launch.bat once first (it sets everything up), then run this again."
}
$at = [datetime]::ParseExact($Time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$(Join-Path $root 'app.py')`" --update" -WorkingDirectory $root
$daily = New-ScheduledTaskTrigger -Daily -At $at
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT5M'   # give Windows and the network a few minutes after signing in
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $daily, $logon -Settings $settings `
    -Principal $principal -Force `
    -Description 'Refreshes CS2 Inventory Intelligence prices and saves the daily snapshot, even when the dashboard is closed.' | Out-Null

Write-Host "Scheduled '$taskName': daily at $($at.ToString('HH:mm')) (or as soon as the PC is on), and 5 minutes after you sign in."
Write-Host "Results are logged to $(Join-Path $root 'data\update.log'). Remove it with: -Remove"
