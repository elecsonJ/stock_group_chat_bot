from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from market_data_provider import MarketDataProvider, PriceQuote


@dataclass(frozen=True)
class MarketDataCapabilities:
    provider_name: str
    execution_grade: bool = False
    supports_realtime_bid_ask: bool = False
    supports_historical_intraday: bool = True
    supports_halts: bool = False
    supports_exchange_calendar: bool = False
    notes: str = ""


@dataclass(frozen=True)
class QuoteRequest:
    ticker: str
    market_hint: str = ""
    require_execution_grade: bool = False


@dataclass(frozen=True)
class HistoricalPriceRequest:
    ticker: str
    when: datetime
    market_hint: str = ""


class MarketDataAdapter(Protocol):
    def capabilities(self) -> MarketDataCapabilities:
        ...

    def get_latest_quote(self, request: QuoteRequest) -> PriceQuote | None:
        ...

    def get_historical_price(self, request: HistoricalPriceRequest) -> PriceQuote | None:
        ...

    def assess_quote_quality(self, quote: PriceQuote | None, *, max_age_minutes: int = 30) -> dict[str, Any]:
        ...


class YFinanceReferenceAdapter:
    """Reference-market-data adapter; not suitable as a final live execution quote source."""

    def __init__(self, provider: MarketDataProvider | None = None):
        self.provider = provider or MarketDataProvider()

    def capabilities(self) -> MarketDataCapabilities:
        return MarketDataCapabilities(
            provider_name="yfinance/Yahoo Finance",
            execution_grade=False,
            supports_realtime_bid_ask=False,
            supports_historical_intraday=True,
            supports_halts=False,
            supports_exchange_calendar=False,
            notes="Reference data for research, paper trading, replay, and readiness checks.",
        )

    def get_latest_quote(self, request: QuoteRequest) -> PriceQuote | None:
        if request.require_execution_grade:
            return None
        ticker = self._with_market_hint(request.ticker, request.market_hint)
        return self.provider.get_latest_quote(ticker)

    def get_historical_price(self, request: HistoricalPriceRequest) -> PriceQuote | None:
        ticker = self._with_market_hint(request.ticker, request.market_hint)
        return self.provider.get_historical_price(ticker, request.when)

    def assess_quote_quality(self, quote: PriceQuote | None, *, max_age_minutes: int = 30) -> dict[str, Any]:
        return self.provider.assess_quote_quality(quote, max_age_minutes=max_age_minutes)

    def _with_market_hint(self, ticker: str, market_hint: str) -> str:
        if not market_hint or ":" in str(ticker):
            return ticker
        return f"{market_hint}:{ticker}"
