param(
    [string]$Root = "",
    [switch]$InstallPlaywright,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
} else {
    $Root = Resolve-Path $Root
}

Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    py -3 -m venv .venv
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

if ($InstallPlaywright) {
    & $VenvPython -m playwright install
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "news_archive") | Out-Null

$EnvPath = Join-Path $Root ".env"
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvPath
    Write-Host "Created .env from .env.example. Edit it before running automation."
}

if ($RunTests) {
    & $VenvPython -m unittest discover -s tests
}

Write-Host "Bootstrap complete."
Write-Host "Python: $VenvPython"
Write-Host "Next: edit .env, then run scripts\windows\install_scheduled_tasks.ps1"
