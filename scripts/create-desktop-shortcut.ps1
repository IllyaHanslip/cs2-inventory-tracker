<#
.SYNOPSIS
    Puts a "CS2 Inventory Intelligence" shortcut on your desktop.

.DESCRIPTION
    The shortcut runs launch.bat, which starts the local server and opens the
    dashboard in your browser. Run it again any time to recreate the shortcut
    (for example after moving the project folder).

        powershell -ExecutionPolicy Bypass -File scripts\create-desktop-shortcut.ps1
        powershell -ExecutionPolicy Bypass -File scripts\create-desktop-shortcut.ps1 -Demo

.PARAMETER Demo
    Create a separate shortcut that opens the app with synthetic demo data.
#>
param([switch]$Demo)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'launch.bat'
$icon = Join-Path $root 'static\icon.ico'
if (-not (Test-Path $launcher)) { throw "launch.bat was not found in $root" }

$name = if ($Demo) { 'CS2 Inventory Intelligence (Demo)' } else { 'CS2 Inventory Intelligence' }
$extra = if ($Demo) { ' --demo' } else { '' }
$desktop = [Environment]::GetFolderPath('Desktop')   # follows OneDrive desktop redirection
$path = Join-Path $desktop "$name.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
# Pointing at cmd.exe (rather than the .bat itself) lets you pin the shortcut to the taskbar/Start.
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\cmd.exe'
$shortcut.Arguments = "/c `"`"$launcher`"$extra`""
$shortcut.WorkingDirectory = $root
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Description = 'Start CS2 Inventory Intelligence and open the dashboard'
$shortcut.WindowStyle = 7   # the launcher window starts minimised
$shortcut.Save()

Write-Host "Created shortcut: $path"
