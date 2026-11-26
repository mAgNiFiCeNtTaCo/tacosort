# Installs TacoSort for the person who runs it (no administrator needed):
#   copies TacoSort.exe to %LOCALAPPDATA%\Programs\TacoSort and adds Start menu and Desktop shortcuts.
# Run it by double-clicking "Install TacoSort.cmd". ASCII only: Windows PowerShell 5.1 reads this file
# in the system code page.
param(
    [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\TacoSort'),
    [string[]]$ShortcutDirs = @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')),
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $here 'TacoSort.exe'
if (-not (Test-Path $source)) {
    Write-Host 'TacoSort.exe is not next to this installer. Unzip the whole folder first, then run Install TacoSort again.'
    exit 1
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
$exe = Join-Path $Target 'TacoSort.exe'
try {
    Copy-Item -Path $source -Destination $exe -Force
} catch {
    Write-Host 'TacoSort could not be replaced because it is running.'
    Write-Host 'Close its window, turn the phone page off in Settings if you use it, then run Install TacoSort again.'
    exit 2
}
foreach ($name in 'uninstall.ps1', 'Uninstall TacoSort.cmd') {
    Copy-Item -Path (Join-Path $here $name) -Destination (Join-Path $Target $name) -Force
}
# Files from a downloaded zip carry a "came from the internet" mark; these are now yours.
Get-ChildItem -Path $Target -File | Unblock-File -ErrorAction SilentlyContinue

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in $ShortcutDirs) {
    if (-not $dir) { continue }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $link = $shell.CreateShortcut((Join-Path $dir 'TacoSort.lnk'))
    $link.TargetPath = $exe
    $link.WorkingDirectory = $Target
    $link.Description = 'TacoSort: tidies your email into folders'
    $link.Save()
}

Write-Host ''
Write-Host "TacoSort is installed in $Target"
Write-Host 'Open it from the Start menu or the TacoSort shortcut on your Desktop.'
Write-Host 'To remove it later: run "Uninstall TacoSort" in that folder.'
if (-not $NoStart) { Start-Process -FilePath $exe -WorkingDirectory $Target }
exit 0
