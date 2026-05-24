# Live-Like Operations Playbook - 2026-05-24

This is the daily operating sequence before the system is allowed anywhere near real capital. It assumes paper mode, human approval, local LLM fact-checking, and reference market data only.

## Pre-Market

1. Confirm local services:

```powershell
.\run_local_healthcheck.bat
.\run_market_data_check.bat
.\run_live_readiness_check.bat --include-network
```

2. Confirm safety defaults:

- kill switch should start `ON`
- `DISCORD_OPERATOR_USER_IDS` should be set
- live broker credentials should not be enabled unless intentionally testing a sandbox
- `SIGNAL_CAPTURE_MARKET_REACTION=false` for routine fast polling, unless you are running a focused validation window

3. Review data quality:

```powershell
.\run_data_quality.bat
```

Do not trade even in paper mode if market data connectivity is failing for the intended market or if recent news/context packs are stale.

## During Market Hours

Routine loop:

```powershell
.\run_news.bat
.\run_news_context.bat
.\run_signals.bat
.\run_debates.bat
```

For events that matter, capture market reaction after enough time has passed:

```powershell
python src\market_reaction_job.py --event-id EVENT_ID
```

Then rerun signals or inspect details so stored reaction snapshots can influence score metadata:

```powershell
.\run_signals.bat
```

Decision rule:

- official/tier-1 evidence first
- local LLM evidence verdict must not be `insufficient` or `contradictory`
- price reaction must not show the move was mostly exhausted before detection, unless the strategy is explicitly momentum continuation
- human approval remains required

## Post-Market

Run reconciliation and feedback:

```powershell
.\run_reconciliation.bat
.\run_replay.bat
.\run_data_quality.bat
```

Review:

- worst paper trades
- already-priced-in events
- signals with weak evidence but strong price reaction
- signals with strong evidence but contradictory price reaction
- market data failures by ticker/market

## Weekly Review

Run replay with a stable horizon and compare:

- hit rate
- average return
- benchmark-relative return
- false positive causes
- data freshness gaps
- market reaction coverage

Only consider sandbox/live broker work after this loop is boringly stable across many paper decisions.

## Live Blockers

Do not enable automatic live orders until these are implemented and tested:

- broker/vendor execution-grade quote adapter
- bid/ask spread and halt checks
- exchange holiday calendar
- broker account/order/fill reconciliation
- max daily loss and drawdown pause
- sandbox order lifecycle parity
- second confirmation path for live order unlock
