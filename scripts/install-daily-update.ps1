<#
.SYNOPSIS
    Runs the daily update (prices, Steam re-sync, snapshot) even on days you don't open the dashboard.

.DESCRIPTION
    Registers a Windows scheduled task, for your user account only, that runs "app.py --update" in the
    background with no window:
      * every hour from 12:00 until midnight - and if the PC was off at one of those times, as soon as
        it's switched on again;
      * 2 minutes after you sign in to Windows.
    Each run decides for itself and usually exits in a second or two, using no internet:
      * before 12:00 it does nothing - automatic updates never start earlier in the day;
      * from 12:00 it runs the update once (at 12:00 if the PC is on, or when you switch it on later);
      * if that couldn't reach the markets (say the internet was down), the next hour tries again;
      * once today's update has happened - by this task, or because you opened the dashboard -
        nothing more happens until tomorrow;
      * while the dashboard is open it does nothing: the dashboard keeps itself up to date.
    If the PC is off all day, that day is skipped and charts carry the last value across.
    Every run is noted in data\update.log. One update downloads about 5-6 MB.
    After each update a Windows notification says what it found (your total and today's change, the
    biggest movers, items that now sell best elsewhere); clicking it opens the dashboard. It also sets
    up the name/icon for those notifications and the link they open, under your account only.

        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1
        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1 -DryRun
        powershell -ExecutionPolicy Bypass -File scripts\install-daily-update.ps1 -Remove

    To use a time other than 12:00, set DAILY_UPDATE_TIME (e.g. 18:00) in .env and pass the same
    time here with -Time 18:00.

.PARAMETER Time
    First run of the day (24-hour clock, default 12:00). Keep it the same as DAILY_UPDATE_TIME in .env.

.PARAMETER Remove
    Delete the scheduled task again.

.PARAMETER DryRun
    Show what would be registered without changing anything.
#>
param([string]$Time = '12:00', [switch]$Remove, [switch]$DryRun)

$ErrorActionPreference = 'Stop'
$taskName = 'CS2 Inventory Intelligence - daily update'
$root = Split-Path -Parent $PSScriptRoot
# Notifications: a name and icon for them, and a "cs2-inventory:" link that opens the dashboard when
# you click one. Both are stored under your own account (HKEY_CURRENT_USER) only.
$appKey = 'HKCU:\Software\Classes\AppUserModelId\CS2InventoryIntelligence'
$linkKey = 'HKCU:\Software\Classes\cs2-inventory'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed the scheduled task '$taskName'."
    } else {
        Write-Host "There is no scheduled task called '$taskName'."
    }
    foreach ($key in $appKey, $linkKey) {
        if (Test-Path $key) { Remove-Item -Path $key -Recurse -Force }
    }
    return
}

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "Python environment not found. Run launch.bat once first (it sets everything up), then run this again."
}
$at = [datetime]::ParseExact($Time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$untilMidnight = $at.Date.AddDays(1).AddMinutes(-1) - $at
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$(Join-Path $root 'app.py')`" --update" -WorkingDirectory $root
# Every day from $Time, repeating hourly until midnight (the hourly retry).
$hourly = New-ScheduledTaskTrigger -Daily -At $at
$hourly.Repetition = (New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration $untilMidnight).Repetition
# Shortly after you sign in (before $Time it simply does nothing).
$signIn = New-ScheduledTaskTrigger -AtLogOn -User $user
$signIn.Delay = 'PT2M'
# StartWhenAvailable: a run missed because the PC was off happens as soon as it's on again.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$description = 'Refreshes CS2 Inventory Intelligence prices and saves the daily snapshot after ' + $at.ToString('HH:mm') +
    ', even when the dashboard is closed. Retries hourly if the markets cannot be reached.'

if ($DryRun) {
    Write-Host "Would register '$taskName' for $user"
    Write-Host "  runs:  $pythonw $($action.Arguments)"
    Write-Host "  in:    $root"
    Write-Host "  when:  daily from $($at.ToString('HH:mm')), every hour for $untilMidnight; and 2 minutes after you sign in"
    Write-Host "  catches up after the PC was off: $($settings.StartWhenAvailable)"
    return
}

New-Item -Path $appKey -Force | Out-Null
Set-ItemProperty -Path $appKey -Name DisplayName -Value 'CS2 Inventory Intelligence'
$icon = Join-Path $root 'static\icon.ico'
if (Test-Path $icon) { Set-ItemProperty -Path $appKey -Name IconUri -Value $icon }
New-Item -Path "$linkKey\shell\open\command" -Force | Out-Null
Set-Item -Path $linkKey -Value 'URL:CS2 Inventory Intelligence'
Set-ItemProperty -Path $linkKey -Name 'URL Protocol' -Value ''
$launcher = Join-Path $root 'launch.bat'
Set-Item -Path "$linkKey\shell\open\command" -Value "`"$env:WINDIR\System32\cmd.exe`" /c `"`"$launcher`"`""

$register = @{ TaskName = $taskName; Action = $action; Settings = $settings; Principal = $principal;
               Description = $description; Force = $true }
try {
    Register-ScheduledTask @register -Trigger $hourly, $signIn | Out-Null
    $when = "after $($at.ToString('HH:mm')) - at that time if the PC is on, or as soon as you sign in later"
} catch [Microsoft.Management.Infrastructure.CimException] {
    # Some PCs only let administrators add sign-in triggers. The hourly trigger still catches
    # up straight after the PC is switched on, because missed runs start as soon as possible.
    Register-ScheduledTask @register -Trigger $hourly | Out-Null
    $when = "after $($at.ToString('HH:mm')) - at that time if the PC is on, or soon after you switch it on later"
}

Write-Host "Done. The daily update now runs once a day $when."
Write-Host "If it can't reach the markets, it tries again every hour until midnight."
Write-Host "Opening the dashboard updates straight away at any time, and then counts as that day's update."
Write-Host "After each background update a notification says what it found; click it to open the dashboard."
Write-Host "(Turn notifications off with NOTIFICATIONS=false in .env.)"
Write-Host "Each run is noted in $(Join-Path $root 'data\update.log'). To undo: add -Remove to the same command."
