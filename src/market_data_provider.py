from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None


@dataclass
class PriceQuote:
    ticker: str
    price: float
    currency: str = ""
    source: str = "unknown"
    quality: str = "unknown"
    as_of: str = ""
    detail: dict[str, Any] | None = None


class MarketDataProvider:
    """
    시장 가격 provider 추상화.

    현재 기본 구현은 yfinance reference quote이며 execution-grade가 아니다.
    추후 broker/live provider는 같은 인터페이스로 교체한다.
    """

    def __init__(self):
        self.provider = os.getenv("MARKET_DATA_PROVIDER", "yfinance").strip().lower()
        self._history_cache: dict[tuple[str, str, str, str], Any] = {}

    def get_latest_quote(self, ticker: str) -> PriceQuote | None:
        if self.provider != "yfinance":
            return None
        return self._get_yfinance_latest_quote(ticker)

    def get_historical_price(self, ticker: str, when: datetime) -> PriceQuote | None:
        if self.provider != "yfinance":
            return None
        return self._get_yfinance_historical_quote(ticker, when)

    def _get_yfinance_latest_quote(self, ticker: str) -> PriceQuote | None:
        if yf is None:
            return None
        normalized = str(ticker or "").upper().strip()
        if not normalized:
            return None
        try:
            info = yf.Ticker(normalized).info
            px = info.get("currentPrice") or info.get("regularMarketPrice")
            if not px or float(px) <= 0:
                return None
            return PriceQuote(
                ticker=normalized,
                price=float(px),
                currency=str(info.get("currency") or info.get("financialCurrency") or ""),
                source="yfinance/Yahoo Finance",
                quality="reference_not_execution_grade",
                as_of=datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                detail={
                    "market_state": info.get("marketState"),
                    "exchange": info.get("exchange"),
                },
            )
        except Exception:
            return None

    def _get_yfinance_historical_quote(self, ticker: str, when: datetime) -> PriceQuote | None:
        if yf is None:
            return None
        normalized = str(ticker or "").upper().strip()
        if not normalized:
            return None
        interval = "15m" if when.hour or when.minute else "1d"
        start = (when - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (when + timedelta(days=7)).strftime("%Y-%m-%d")
        cache_key = (normalized, interval, start, end)
        frame = self._history_cache.get(cache_key)
        if frame is None:
            try:
                frame = yf.Ticker(normalized).history(start=start, end=end, interval=interval)
            except Exception:
                frame = None
            self._history_cache[cache_key] = frame
        if frame is None or getattr(frame, "empty", True):
            return None
        try:
            indexed = frame.sort_index()
            target = when.replace(tzinfo=None)
            chosen = None
            chosen_ts = None
            for idx, row in indexed.iterrows():
                ts = idx.to_pydatetime().replace(tzinfo=None)
                if ts >= target:
                    chosen = row
                    chosen_ts = ts
                    break
            if chosen is None:
                chosen = indexed.iloc[-1]
                chosen_ts = indexed.index[-1].to_pydatetime().replace(tzinfo=None)
            close_value = chosen.get("Close")
            if close_value is None or float(close_value) <= 0:
                return None
            return PriceQuote(
                ticker=normalized,
                price=float(close_value),
                source="yfinance/Yahoo Finance",
                quality="historical_reference_not_execution_grade",
                as_of=chosen_ts.strftime('%Y-%m-%dT%H:%M:%S') if chosen_ts else "",
                detail={"interval": interval},
            )
        except Exception:
            return None
