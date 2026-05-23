param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("bot", "news", "news_backfill", "news_context", "signals", "debates", "replay", "data_quality", "market_reaction", "reconciliation", "live_readiness", "daily", "weekly", "monthly", "healthcheck", "maintenance")]
    [string]$Job,
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
} else {
    $Root = Resolve-Path $Root
}

Set-Location $Root

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "$Job`_$Stamp.log"

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

$JobArgs = @{
    "bot"           = @("src\main.py")
    "news"          = @("src\scraper_job.py")
    "news_backfill" = @("src\scraper_job.py", "--backfill", "48")
    "news_context"  = @("src\news_context_job.py")
    "signals"       = @("src\signal_job.py")
    "debates"       = @("src\debate_job.py")
    "replay"        = @("src\replay_job.py")
    "data_quality"  = @("src\data_quality_job.py")
    "market_reaction" = @("src\market_reaction_job.py")
    "reconciliation" = @("src\reconciliation_job.py")
    "live_readiness" = @("src\live_readiness_check.py")
    "daily"         = @("src\summarizer.py", "daily")
    "weekly"        = @("src\summarizer.py", "weekly")
    "monthly"       = @("src\summarizer.py", "monthly")
    "healthcheck"   = @("src\local_model_healthcheck.py")
    "maintenance"   = @("src\maintenance_job.py")
}

Write-Host "[$(Get-Date -Format o)] job=$Job root=$Root python=$Python"
Write-Host "Log: $LogFile"

try {
    & $Python @($JobArgs[$Job]) 2>&1 | Tee-Object -FilePath $LogFile
    $ExitCode = $LASTEXITCODE
    if ($null -eq $ExitCode) {
        $ExitCode = 0
    }
} catch {
    $_ | Out-String | Tee-Object -FilePath $LogFile -Append
    $ExitCode = 1
}

Write-Host "[$(Get-Date -Format o)] job=$Job exit=$ExitCode"
exit $ExitCode
