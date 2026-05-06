param(
    [string]$Root = "",
    [string]$TaskPrefix = "StockBot",
    [switch]$IncludeBotOnLogon,
    [switch]$IncludeReplayHourly,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
} else {
    $Root = Resolve-Path $Root
}

$TaskCommands = @{
    "bot"           = Join-Path $Root "run_bot.bat"
    "news"          = Join-Path $Root "run_news.bat"
    "news_context"  = Join-Path $Root "run_news_context.bat"
    "news_backfill" = Join-Path $Root "run_news_backfill.bat"
    "signals"       = Join-Path $Root "run_signals.bat"
    "debates"       = Join-Path $Root "run_debates.bat"
    "replay"        = Join-Path $Root "run_replay.bat"
    "daily"         = Join-Path $Root "run_daily.bat"
    "weekly"        = Join-Path $Root "run_weekly.bat"
    "monthly"       = Join-Path $Root "run_monthly.bat"
    "healthcheck"   = Join-Path $Root "run_local_healthcheck.bat"
    "maintenance"   = Join-Path $Root "run_maintenance.bat"
}

function Register-Schtask {
    param(
        [string]$Name,
        [string[]]$ScheduleArgs,
        [string]$Job
    )
    $TaskName = "\$TaskPrefix\$Name"
    $TaskPath = $TaskCommands[$Job]
    if (-not (Test-Path $TaskPath)) {
        throw "Task command not found: $TaskPath"
    }
    $TaskRun = "`"$TaskPath`""
    if ($WhatIfOnly) {
        Write-Host "schtasks /Create /F /TN `"$TaskName`" /TR `"$TaskRun`" $($ScheduleArgs -join ' ')"
    } else {
        & schtasks /Create /F /TN $TaskName /TR $TaskRun @ScheduleArgs
    }
}

Register-Schtask -Name "NewsPoll" -ScheduleArgs @("/SC", "MINUTE", "/MO", "10") -Job "news"
Register-Schtask -Name "NewsContextPack" -ScheduleArgs @("/SC", "MINUTE", "/MO", "30") -Job "news_context"
Register-Schtask -Name "Signals" -ScheduleArgs @("/SC", "MINUTE", "/MO", "15") -Job "signals"
Register-Schtask -Name "Debates" -ScheduleArgs @("/SC", "MINUTE", "/MO", "15") -Job "debates"
Register-Schtask -Name "NewsBackfill" -ScheduleArgs @("/SC", "DAILY", "/ST", "07:00") -Job "news_backfill"
Register-Schtask -Name "DailySummary" -ScheduleArgs @("/SC", "DAILY", "/ST", "23:30") -Job "daily"
Register-Schtask -Name "WeeklySummary" -ScheduleArgs @("/SC", "WEEKLY", "/D", "SUN", "/ST", "23:40") -Job "weekly"
Register-Schtask -Name "MonthlySummary" -ScheduleArgs @("/SC", "MONTHLY", "/D", "1", "/ST", "23:50") -Job "monthly"
Register-Schtask -Name "LocalHealthcheck" -ScheduleArgs @("/SC", "DAILY", "/ST", "08:30") -Job "healthcheck"
Register-Schtask -Name "Maintenance" -ScheduleArgs @("/SC", "DAILY", "/ST", "03:30") -Job "maintenance"

if ($IncludeReplayHourly) {
    Register-Schtask -Name "ReplayHourly" -ScheduleArgs @("/SC", "HOURLY", "/MO", "1") -Job "replay"
}

if ($IncludeBotOnLogon) {
    Register-Schtask -Name "DiscordBotOnLogon" -ScheduleArgs @("/SC", "ONLOGON") -Job "bot"
}

Write-Host "Scheduled task registration complete. Prefix: $TaskPrefix"
