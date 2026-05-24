# User-Required Integrations - 2026-05-24

These items require accounts, credentials, contracts, or personal trading decisions. They cannot be completed safely by code alone.

## 1. Choose The Real Market Data Source

Current code has a reference adapter and an execution-grade adapter contract, but no real broker/vendor feed is configured.

You need to choose one:

- Alpaca market data
- Interactive Brokers
- Korea Investment Securities
- another broker/vendor with quote, bid/ask, market status, and historical data APIs

Minimum required data:

- last trade price
- bid/ask
- quote timestamp
- exchange/trading status
- halted/limit state if available
- provider delay status

After choosing the provider, set:

```env
MARKET_DATA_ADAPTER=<provider_name>
REQUIRE_EXECUTION_GRADE_MARKET_DATA=true
```

## 2. Choose The Broker And First Trading Universe

Pick one first universe instead of mixed global live trading immediately:

- US equities/ETFs
- Korean equities/ETFs
- paper-only mixed global watchlist

Also decide:

- sandbox first or paper only
- max capital for first live phase
- allowed order types
- regular hours only or extended hours
- short selling allowed or not

## 3. Provide Credentials Outside Git

Credentials belong only in local `.env` or the broker's secure config. Never commit them.

Likely credentials:

```env
BROKER_API_KEY=
BROKER_API_SECRET=
BROKER_ACCOUNT_ID=
BROKER_BASE_URL=
MARKET_DATA_API_KEY=
MARKET_DATA_API_SECRET=
```

Exact names will depend on the provider selected.

## 4. Decide Operational Guardrails

Before live/sandbox orders, decide these numbers:

```env
RISK_DEFAULT_POSITION_PCT=
RISK_MAX_TICKER_EXPOSURE_PCT=
RISK_MAX_GROSS_EXPOSURE_PCT=
RISK_MAX_OPEN_POSITIONS=
RISK_TICKER_COOLDOWN_MIN=
```

Still missing for true live mode:

- max daily loss
- max drawdown pause
- live unlock confirmation process
- strategy universe allowlist

## 5. Confirm Discord Operator IDs

Set this so approval and kill-switch commands are restricted:

```env
DISCORD_OPERATOR_USER_IDS=123456789012345678,234567890123456789
```

## What Is Already Done In Code

- Market-aware ticker resolution
- Reference yfinance adapter
- Future execution-grade adapter contract
- Exchange calendar integration with fallback
- Market data connectivity job
- Readiness warning when execution-grade data is required but unavailable
- Market reaction scoring with volume z-score and benchmark/sector comparison
- Paper mode quality gate for optional execution-grade quote requirement

## Recommended Next User Decision

Choose the first live/sandbox target:

1. US-only with Alpaca or IBKR
2. Korea-only with a Korean broker API
3. stay paper-only until the strategy has more replay/paper evidence

The safest path is option 3 until paper/replay results are stable, then option 1 or 2 with tiny sandbox/live size.
