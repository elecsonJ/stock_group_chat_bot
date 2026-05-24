# Market Data Connectivity - 2026-05-24

## Verdict

The project now has a market-aware reference quote layer for paper trading, replay, event reaction analysis, data quality checks, and research context. It is much better than passing raw tickers directly to Yahoo Finance, but it is still not an execution-grade live market data feed.

Use it for:

- reference current prices
- historical replay prices
- event-window market reaction snapshots
- portfolio/PnL context
- pre-live connectivity checks
- AI debate/fact-sheet market context
- market session metadata for regular-hours sanity checks
- sector/market benchmark selection for event-reaction analysis

Do not treat it as sufficient for unattended live trading. Live execution still needs broker-grade quotes, exchange calendars, real-time/near-real-time bid/ask data, and broker reconciliation.

## Supported Ticker Input

`MarketDataProvider` accepts these practical forms:

- United States: `NVDA`, `AAPL`, `MSFT`
- Korea KOSPI/KOSDAQ: `005930`, `005930.KS`, `035720.KQ`, `KR:005930`, `KOSDAQ:035720`
- Japan: `JP:7203`, `7203.T`
- Hong Kong: `HK:700`, `0700.HK`
- China: `CN:600519`, `SH:600519`, `SZ:000001`, `600519.SS`, `000001.SZ`
- United Kingdom: `LSE:VOD`, `VOD.L`
- Canada: `TSX:SHOP`, `SHOP.TO`
- Australia, India, Germany, France, Singapore, Brazil, Mexico through common Yahoo Finance suffixes
- Global symbols that already use Yahoo syntax, such as `BTC-USD`, `EURUSD=X`, `^GSPC`

For ambiguous numeric tickers, prefer a market hint. For example:

- Good: `KR:005930`, `KOSDAQ:035720`, `HK:700`, `JP:7203`
- Risky: `700`, `7203`, `5930`

PowerShell note: wrap comma-separated tickers in quotes so leading zeros are preserved.

```powershell
python src\market_data_check.py --tickers "NVDA,005930,HK:700,JP:7203"
```

## Market Benchmarks

Market reaction analysis now uses a market-specific benchmark when the caller does not provide one:

- US: `SPY`
- Korea KOSPI: `069500.KS`
- Korea KOSDAQ: `229200.KS`
- Japan: `1306.T`
- Hong Kong: `2800.HK`
- China Shanghai: `510300.SS`
- China Shenzhen: `159919.SZ`
- Canada: `XIU.TO`
- Australia: `STW.AX`
- India: `NIFTYBEES.NS`
- Brazil: `BOVA11.SA`

This matters because comparing a Korean stock's post-news move to `SPY` can create a false relative-return interpretation.

For US stocks, `MarketDataProvider.sector_benchmark_for_ticker()` also maps common sectors to liquid ETFs. Semiconductor names map to `SOXX`; other major sectors map to ETFs such as `XLK`, `XLF`, `XLV`, `XLE`, `XLY`, `XLP`, `XLI`, `XLU`, `XLRE`, `XLC`, and `XLB`.

Korean sector ETFs are not hard-coded because sector ETF choice can be strategy-specific. Set this if a default is useful:

```env
KOREA_SECTOR_BENCHMARK_DEFAULT=
```

## Market Reaction Scoring

`MarketReactionAnalyzer` now records:

- pre/post event price windows
- market benchmark relative return
- optional sector benchmark relative return in `detail_json`
- `volume_zscore`
- `already_priced_in`
- score adjustment rationale

`SignalEngine` reads saved reaction snapshots when `SIGNAL_MARKET_REACTION_SCORING=true` and adjusts signal score metadata. This avoids forcing every signal run to make slow market-data calls, while still letting accumulated price reaction data improve later decisions.

```env
SIGNAL_CAPTURE_MARKET_REACTION=false
SIGNAL_MARKET_REACTION_SCORING=true
```

## Operational Checks

Run a direct market data connectivity check:

```powershell
.\run_market_data_check.bat
```

Or run custom samples:

```powershell
python src\market_data_check.py --tickers "NVDA,005930,KOSDAQ:035720,HK:700,JP:7203"
```

The latest report is saved in DB metadata as:

```text
market_data_connectivity_report_v1
```

`run_data_quality.bat` now includes this report in the data quality score. `run_live_readiness_check.bat --include-network` checks multiple markets through `READINESS_TICKERS`.

## Environment

```env
MARKET_DATA_PROVIDER=yfinance
YFINANCE_CACHE_DIR=data/yfinance_cache
READINESS_TICKERS=SPY,005930,HK:700,JP:7203
MARKET_DATA_CHECK_TICKERS=NVDA,005930,KOSDAQ:035720,HK:700,JP:7203
```

## Remaining Live-Trading Gap

For real money, this still needs a broker or market-data vendor layer that can provide:

- real-time or delayed quote status explicitly
- bid/ask spread and last trade
- exchange trading calendar and holidays
- auction/halts/limit-up/limit-down states
- corporate action adjustments
- rate-limit and outage reporting
- order-time quote snapshot tied to execution

The current layer is appropriate as a resilient reference provider, not as the final source of truth for order placement.

The adapter interface for a future broker/vendor feed is in `src/market_data_adapter.py`. A live adapter should implement bid/ask, halt status, exchange calendar, and execution-grade freshness semantics before automatic live orders are enabled.
