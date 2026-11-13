<#
.SYNOPSIS
    Keeps prices and the daily snapshot up to date even on days you don't open the dashboard.

.DESCRIPTION
    Registers a Windows scheduled task (for your user account only, no admin rights needed) that
    runs "app.py --update" in the background, with no window, every 2 hours while the PC is on
    (and straight away if the PC was off at a scheduled time). Each run checks first and exits
    within a second or two if there's nothing to do: today's snapshot is saved and prices are
    under 12 hours old, or the dashboard is open (it updates itself). So the real work happens
    about twice a day. If the PC is off all day, that day is skipped: charts carry the last
    value across the gap. What it did is written to data\update.log.

        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1
        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1 -Remove

.PARAMETER Remove
    Delete the scheduled task again.

.PARAMETER DryRun
    Show what would be registered without changing anything.
#>
param([switch]$Remove, [switch]$DryRun)

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
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$(Join-Path $root 'app.py')`" --update" -WorkingDirectory $root
# Every day from midnight, repeating every 2 hours.
$trigger = New-ScheduledTaskTrigger -Daily -At '00:00'
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Hours 23 -Minutes 59)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

if ($DryRun) {
    Write-Host "Would register '$taskName' for $user"
    Write-Host "  runs:    $pythonw $($action.Arguments)"
    Write-Host "  in:      $root"
    Write-Host "  when:    daily from $($trigger.StartBoundary), every $($trigger.Repetition.Interval) for $($trigger.Repetition.Duration)"
    Write-Host "  catches up after missed runs: $($settings.StartWhenAvailable)"
    return
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'Refreshes CS2 Inventory Intelligence prices and saves the daily snapshot, even when the dashboard is closed.' | Out-Null

Write-Host "Scheduled '$taskName': every 2 hours while the PC is on (it only does real work about twice a day)."
Write-Host "Results are logged to $(Join-Path $root 'data\update.log'). Remove it with: -Remove"
