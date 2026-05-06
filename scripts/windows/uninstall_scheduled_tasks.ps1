param(
    [string]$TaskPrefix = "StockBot",
    [switch]$WhatIfOnly
)

$Names = @(
    "NewsPoll",
    "NewsContextPack",
    "Signals",
    "Debates",
    "NewsBackfill",
    "DailySummary",
    "WeeklySummary",
    "MonthlySummary",
    "LocalHealthcheck",
    "Maintenance",
    "ReplayHourly",
    "DiscordBotOnLogon"
)

foreach ($Name in $Names) {
    $TaskName = "\$TaskPrefix\$Name"
    if ($WhatIfOnly) {
        Write-Host "schtasks /Delete /F /TN `"$TaskName`""
    } else {
        & schtasks /Delete /F /TN $TaskName
    }
}
