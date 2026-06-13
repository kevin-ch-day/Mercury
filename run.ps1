# Mercury launcher for Windows — use from repo root: .\run.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Venv = Join-Path $Root ".venv"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$Stamp = Join-Path $Venv ".mercury-sync-stamp"
$SkipSync = if ($env:MERCURY_SKIP_SYNC) { $env:MERCURY_SKIP_SYNC } else { "0" }

function Require-Mercury {
    $Mercury = Join-Path $Venv "Scripts\mercury.exe"
    if (-not (Test-Path $Mercury)) {
        Write-Error "Mercury is not installed in $Venv. Run: pip install -e `".[mariadb,dev]`""
    }
}

if (-not (Test-Path $Venv)) {
    Write-Host "Creating virtual environment in .venv ..."
    & $Python -m venv $Venv
}

$NeedsInstall = $false
if ($SkipSync -ne "1") {
    $MercuryExe = Join-Path $Venv "Scripts\mercury.exe"
    if (-not (Test-Path $MercuryExe)) { $NeedsInstall = $true }
    elseif (-not (Test-Path $Stamp)) { $NeedsInstall = $true }
    elseif ((Get-Item "pyproject.toml").LastWriteTime -gt (Get-Item $Stamp).LastWriteTime) { $NeedsInstall = $true }
    elseif ((Get-Item "src").LastWriteTime -gt (Get-Item $Stamp).LastWriteTime) { $NeedsInstall = $true }
}

if ($NeedsInstall) {
    Write-Host "Syncing Mercury virtualenv ..."
    & (Join-Path $Venv "Scripts\pip.exe") install -e ".[mariadb,dev]"
    New-Item -ItemType File -Path $Stamp -Force | Out-Null
}

Require-Mercury

$MercuryCmd = Join-Path $Venv "Scripts\mercury.exe"
if ($args.Count -eq 0) {
    & $MercuryCmd menu
} else {
    & $MercuryCmd @args
}
