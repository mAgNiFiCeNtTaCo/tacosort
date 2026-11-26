# Removes what "Install TacoSort" put on this computer: the program, its shortcuts, and (only when they
# run this copy) its background sorting and phone page. Your mail, your folders and your settings stay.
# ASCII only: Windows PowerShell 5.1 reads this file in the system code page.
param(
    [string]$Target = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string[]]$ShortcutDirs = @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')),
    [switch]$Yes
)
$ErrorActionPreference = 'Continue'
# The long form of the path (C:\Users\LONGNA~1 -> C:\Users\Long Name): shortcuts and tasks store that one.
$Target = [IO.Path]::GetFullPath($Target)
$exe = Join-Path $Target 'TacoSort.exe'

function Test-SamePath([string]$a, [string]$b) {
    if (-not $a -or -not $b) { return $false }
    try { return [IO.Path]::GetFullPath($a.Trim('"')) -ieq [IO.Path]::GetFullPath($b.Trim('"')) } catch { return $false }
}

if (-not $Yes) {
    Write-Host 'This removes TacoSort from this computer. Your mail and folders are not touched.'
    Write-Host 'Tip: to also delete the saved mail passwords, open TacoSort first and remove each account on the Accounts page.'
    $answer = Read-Host 'Remove TacoSort now? Type y and press Enter'
    if ($answer -notmatch '^(y|yes)$') { Write-Host 'Nothing was removed.'; exit 0 }
}

# Background sorting and the phone page: switch them off only when they run THIS copy of TacoSort.
$task = Get-ScheduledTask -TaskName "TacoSort-$env:USERNAME" -ErrorAction SilentlyContinue
if ($task -and ($task.Actions | Where-Object { Test-SamePath $_.Execute $exe })) {
    Start-Process -FilePath $exe -ArgumentList 'schedule', 'off' -Wait -ErrorAction SilentlyContinue
    Write-Host 'Background sorting is switched off.'
}
$run = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).'TacoSort phone page'
if ($run -and $run.ToLower().Contains($exe.ToLower())) {
    Start-Process -FilePath $exe -ArgumentList 'phone', 'off' -Wait -ErrorAction SilentlyContinue
    Write-Host 'The phone page is switched off.'
}

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in $ShortcutDirs) {
    if (-not $dir) { continue }
    $path = Join-Path $dir 'TacoSort.lnk'
    if ((Test-Path $path) -and (Test-SamePath $shell.CreateShortcut($path).TargetPath $exe)) { Remove-Item -Path $path -Force }
}

$failed = $false
foreach ($name in 'TacoSort.exe', 'Uninstall TacoSort.cmd', 'uninstall.ps1') {
    $path = Join-Path $Target $name
    if (Test-Path $path) {
        try { Remove-Item -Path $path -Force -ErrorAction Stop } catch { $failed = $true }
    }
}
if ($failed) {
    Write-Host 'TacoSort is still running, so its file could not be removed. Close its window and run this again.'
    exit 2
}
# The folder goes only when nothing else is in it.
if ((Test-Path $Target) -and -not (Get-ChildItem -Path $Target -Force)) { Remove-Item -Path $Target -Force }

Write-Host ''
Write-Host 'TacoSort is removed. Your settings are kept in case you install it again;'
Write-Host 'to delete them too, remove the TacoSort folder in %APPDATA%.'
exit 0
