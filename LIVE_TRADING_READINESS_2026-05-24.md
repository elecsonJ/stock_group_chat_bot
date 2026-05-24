# Live Trading Readiness Checklist (2026-05-24)

This document defines what must be true before this project should move from research/paper mode into sandbox or live trading. The current system is useful as a research, debate, paper-execution, replay, and feedback engine. It is not yet a fully production-ready live trading system.

## Current Readiness Verdict

Status: `not ready for unattended live trading`

Reason: the research and paper workflow is now fairly structured, but live trading needs stronger market data, broker integration, operational monitoring, and rollout controls.

Safe current use:

- News collection and structured event memory
- Evidence package generation
- Local LLM assisted fact-checking and evidence verdicts
- Frontier model debate with evidence context
- Human-approved paper execution
- Replay and performance feedback
- Data quality evaluation
- Market-aware reference price checks across common US/KR/JP/HK/CN/global Yahoo Finance formats
- Live-like daily operating sequence documented in `LIVE_OPERATIONS_PLAYBOOK_2026-05-24.md`

Do not enable yet:

- Fully automatic live orders
- Large capital allocation
- Direct Discord command to live broker without a second confirmation channel
- Trading decisions based only on news text without price/volume reaction context

## Practical Things Needed Before Live Use

### 1. Broker And Account Setup

Required decisions:

- Broker target: Alpaca, IBKR, Korea Investment Securities, or another provider
- First mode: sandbox/mock only
- Tradable universe: US equities only, Korea equities only, ETFs, or mixed
- Order types allowed: market, limit, stop, bracket/OCO
- Allowed sessions: regular hours only or extended hours

Required implementation:

- Broker adapter implementing the existing `BrokerAdapter` interface
- Account snapshot sync: cash, equity, buying power
- Position sync: quantity, average price, market value, unrealized PnL
- Open order sync: submitted, partially filled, filled, canceled, rejected
- Fill sync: execution price, quantity, timestamp, broker order id
- Reconciliation job that compares local DB state with broker state

Minimum live gate:

- Paper broker and sandbox broker must produce matching state transitions for the same test orders.
- Any sync mismatch must block new orders until reviewed.

### 2. Market Data

Current state:

- `MarketDataProvider` uses yfinance/Yahoo Finance reference quotes and historical closes.
- `MarketDataProvider` now resolves common market-specific ticker forms; see `MARKET_DATA_CONNECTIVITY_2026-05-24.md`.
- `run_market_data_check.bat` verifies current cross-market connectivity and stores `market_data_connectivity_report_v1`.
- `src/market_data_adapter.py` defines the adapter contract for a future broker/vendor execution-grade feed.
- This is acceptable for paper/replay/reference checks.
- It is not execution-grade quote data.

Needed before live:

- A paid or broker-provided market data source for live bid/ask, last trade, volume, and market status
- Clear timestamp and staleness checks
- Market hours and holiday calendar
- Per-ticker data quality flags
- Fallback behavior when price is stale, missing, delayed, or inconsistent

Market reaction layer needed:

- For each news event, persist a price/volume snapshot around the event time.
- Compare ticker move against benchmark and sector ETF.
- Detect whether the move started before public news collection.
- Record if the event looks already priced in.

Suggested event-window fields:

- `event_id`
- `ticker`
- `event_time`
- `price_before_5m`, `price_after_5m`, `price_after_30m`, `price_after_1h`, `price_after_1d`
- `volume_zscore`
- `benchmark_return`
- `sector_return`
- `relative_return`
- `pre_news_move_pct`
- `already_priced_in_flag`
- `market_data_quality`

### 3. Risk Controls

Already present:

- Kill switch
- Daily/hourly order limit
- Position sizing skeleton
- Exposure constraints
- Approval-based execution

Needed before live:

- Max daily loss
- Max drawdown pause
- Per-symbol max notional
- Per-sector max exposure
- Per-strategy max exposure
- Volatility-adjusted sizing
- No-trade rules for earnings, CPI/FOMC, market halts, extreme spreads
- Cooldown after consecutive losses
- Separate sandbox/live config

Minimum live gate:

- Default live mode must start with `kill_switch=ON`.
- Live mode must require explicit manual unlock.
- First live phase should be tiny notional only.

### 4. Decision Quality Gates

Current local LLM recommendation:

- Use e4b for fast/high-volume helper tasks.
- Use 31B for `evidence_verdict`, `judge`, and high-stakes evidence summaries.

Needed before live:

- Evidence verdict must be `ready_for_signal=true`.
- Source tier must include official, company IR, regulatory, or tier-1 media for material claims.
- Market reaction snapshot must not show a fully exhausted move unless the strategy explicitly supports momentum continuation.
- Debate output must include dissent or risk scenario.
- Final action must remain human-approved until enough live-like sandbox data is accumulated.

Minimum live gate:

- No order should be submitted from an event with `verification.verdict != verified`.
- No order should be submitted without a fresh price snapshot.
- No order should be submitted if the local 31B evidence verdict says `insufficient` or `contradictory`.

### 5. Replay And Paper Track Record

Needed before live:

- Minimum 30 to 100 paper decisions across different market regimes
- Out-of-sample replay split
- Benchmark comparison
- False positive taxonomy
- Strategy-level attribution
- Slippage/spread sensitivity

Useful thresholds before sandbox:

- Positive expectancy after estimated slippage
- Drawdown within predefined tolerance
- No severe data quality gaps for executed paper trades
- Manual review of worst trades

Useful thresholds before tiny live:

- Sandbox order sync stable
- No missed cancel/fill state
- No stale-price orders in dry run
- Human operator can reproduce every decision from stored context

### 6. Monitoring And Operations

Needed before live:

- Healthcheck job for local LLM, frontier APIs, DB, news sources, market data, and broker
- Alert channel for failures
- Daily run summary
- Audit log for every action
- Backup and restore procedure for SQLite DB
- Secret rotation plan
- Dependency install procedure

Minimum live gate:

- If any critical dependency fails, the system must fail closed.
- If broker state cannot be reconciled, new orders must stop.
- If market data is stale, new orders must stop.

### 7. Security And Secrets

Required:

- `.env` must stay untracked.
- Broker keys must be sandbox first.
- Live keys should use least privilege where the broker supports it.
- Withdrawal permissions should never be enabled for automation credentials.
- Discord commands that can affect orders should be restricted to explicit user IDs or roles.

### 8. User Decisions Needed

The user must decide:

- Broker/provider priority
- Trading universe
- Starting paper capital and tiny-live capital
- Max loss per day
- Max loss per trade
- Max position size
- Whether shorts are allowed
- Whether extended-hours trading is allowed
- How much delay is acceptable for 31B local model calls
- Whether market data should be paid/broker-grade or reference-grade for the next phase

## Recommended Next Implementation Order

1. Event-window market reaction snapshots
2. Market data quality/staleness gates
3. Broker sandbox adapter
4. Broker state reconciliation job
5. Live readiness healthcheck command
6. Role-restricted Discord execution controls
7. Tiny-live rollout checklist

## Implemented Local Hardening (2026-05-24)

These items can run without external broker credentials:

- `src/market_reaction.py`
  - Captures event-window price reaction snapshots for signal events.
  - Stores pre/post event price moves, benchmark-relative return, and an `already_priced_in` flag.
- `src/market_reaction_job.py` and `run_market_reaction.bat`
  - Batch entry point for filling market reaction snapshots.
- `MarketDataProvider.assess_quote_quality`
  - Adds a basic market data quality/staleness gate.
- `TradingExecutor`
  - Blocks paper execution when market data is missing or stale.
- `src/reconciliation.py`
  - Checks paper account, positions, orders, and fills for internal state mismatch.
- `src/reconciliation_job.py` and `run_reconciliation.bat`
  - Batch entry point for reconciliation.
- `src/live_readiness_check.py` and `run_live_readiness_check.bat`
  - Reports DB, kill switch, environment, reconciliation, recent data, and optional market data status.
- Discord operator guard
  - Dangerous execution commands can be restricted with `DISCORD_OPERATOR_USER_IDS`.
- `src/mock_broker_adapter.py`
  - In-memory broker lifecycle simulator for adapter contract tests.
- `SIGNAL_CAPTURE_MARKET_REACTION=false`
  - Optional signal-generation hook for capturing market reaction snapshots immediately after signal upsert. Keep it off for fast routine runs, turn it on for deeper paper/sandbox runs.

Current local validation:

```text
python -m pytest -q
46 passed, 2 skipped
```

## Go/No-Go Rule

The system can move to sandbox trading when:

- Paper execution is stable
- Market reaction snapshots are stored
- Data quality report is acceptable
- 31B evidence verdict is active for high-stakes gates
- Broker sandbox adapter passes order lifecycle tests
- Kill switch and failure gates are verified

The system can move to tiny live trading only after sandbox mode runs without state mismatch and every live order still requires explicit human approval.
