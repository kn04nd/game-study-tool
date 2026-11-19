# Admin-only steps of the Game Study Toolkit install. Run elevated (install.ps1 -EnableLongPaths / -System do it).
#  1. LongPathsEnabled = 1 (deep extracted asset trees exceed 260 chars)
#  2. Microsoft Defender exclusion for the study root ONLY (never for the tools folder); skipped with -LongPathsOnly
param(
    [string]$GsHome = '',
    [switch]$LongPathsOnly
)
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'system-setup.ps1 must run elevated (Run as administrator).'
    exit 1
}
if (-not $LongPathsOnly -and -not $GsHome) {
    Write-Error 'system-setup.ps1 needs -GsHome <study root> (or -LongPathsOnly).'
    exit 1
}

Write-Host '==> Enabling Win32 long paths (HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1)'
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -Type DWord
if ($LongPathsOnly) {
    Write-Host '==> Done (long paths only).'
    exit 0
}

if (-not (Test-Path -LiteralPath $GsHome)) {
    New-Item -ItemType Directory -Path $GsHome -Force | Out-Null
}
$full = (Resolve-Path -LiteralPath $GsHome).Path
Write-Host "==> Adding a Microsoft Defender exclusion for the study root only: $full"
try {
    Add-MpPreference -ExclusionPath $full
} catch {
    Write-Warning "Could not add the Defender exclusion (another antivirus or policy?): $($_.Exception.Message)"
}
Write-Host '==> Done. Tools under %LOCALAPPDATA%\game-study-tool\tools stay scanned by design.'
