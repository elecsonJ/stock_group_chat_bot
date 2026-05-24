from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

from yfinance_runtime import configure_yfinance_cache


@dataclass
class PriceQuote:
    ticker: str
    price: float
    currency: str = ""
    source: str = "unknown"
    quality: str = "unknown"
    as_of: str = ""
    detail: dict[str, Any] | None = None


@dataclass(frozen=True)
class MarketInstrument:
    requested_ticker: str
    provider_ticker: str
    market: str = "unknown"
    country: str = ""
    exchange_hint: str = ""
    currency_hint: str = ""
    benchmark_ticker: str = "SPY"
    timezone: str = "America/New_York"
    regular_open: str = "09:30"
    regular_close: str = "16:00"


class MarketDataProvider:
    """
    시장 가격 provider 추상화.

    현재 기본 구현은 yfinance reference quote이며 execution-grade가 아니다.
    추후 broker/live provider는 같은 인터페이스로 교체한다.
    """

    def __init__(self):
        self.provider = os.getenv("MARKET_DATA_PROVIDER", "yfinance").strip().lower()
        self._history_cache: dict[tuple[str, str, str, str], Any] = {}
        self._resolution_cache: dict[str, MarketInstrument | None] = {}
        configure_yfinance_cache(yf)

    def get_latest_quote(self, ticker: str) -> PriceQuote | None:
        if self.provider != "yfinance":
            return None
        return self._get_yfinance_latest_quote(ticker)

    def assess_quote_quality(self, quote: PriceQuote | None, *, max_age_minutes: int = 30) -> dict[str, Any]:
        if quote is None:
            return {
                "state": "missing",
                "tradable": False,
                "reasons": ["missing_quote"],
            }
        reasons = []
        state = "reference"
        as_of = self._parse_dt(quote.as_of)
        if as_of:
            age_min = max(0.0, (datetime.now() - as_of).total_seconds() / 60.0)
            if age_min > max_age_minutes:
                state = "stale"
                reasons.append(f"stale_quote_{age_min:.1f}m")
        else:
            reasons.append("missing_as_of")
        detail = quote.detail or {}
        market_state = str(detail.get("market_state", "") or "").upper()
        if market_state and market_state not in {"REGULAR", "OPEN"}:
            reasons.append(f"market_state_{market_state.lower()}")
        if quote.price <= 0:
            state = "missing"
            reasons.append("non_positive_price")
        tradable = state not in {"missing", "stale"} and quote.price > 0
        return {
            "state": state,
            "tradable": tradable,
            "reasons": reasons,
            "source": quote.source,
            "quality": quote.quality,
            "as_of": quote.as_of,
            "price": quote.price,
        }

    def get_historical_price(self, ticker: str, when: datetime) -> PriceQuote | None:
        if self.provider != "yfinance":
            return None
        return self._get_yfinance_historical_quote(ticker, when)

    def get_history_frame(
        self,
        ticker: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
        interval: str = "1d",
    ) -> Any:
        if self.provider != "yfinance" or yf is None:
            return None
        instrument = self.resolve_instrument(ticker)
        if not instrument:
            return None
        cache_start = start.strftime("%Y-%m-%dT%H:%M:%S") if start else ""
        cache_end = end.strftime("%Y-%m-%dT%H:%M:%S") if end else ""
        cache_key = (instrument.provider_ticker, interval, period or cache_start, cache_end)
        if cache_key in self._history_cache:
            return self._history_cache[cache_key]
        try:
            ticker_obj = yf.Ticker(instrument.provider_ticker)
            if period:
                frame = ticker_obj.history(period=period, interval=interval)
            else:
                kwargs: dict[str, Any] = {"interval": interval}
                if start:
                    kwargs["start"] = start.strftime("%Y-%m-%d")
                if end:
                    kwargs["end"] = end.strftime("%Y-%m-%d")
                frame = ticker_obj.history(**kwargs)
        except Exception:
            frame = None
        self._history_cache[cache_key] = frame
        return frame

    def session_state(self, ticker: str, when: datetime | None = None) -> dict[str, Any]:
        instrument = self.resolve_instrument(ticker)
        if not instrument:
            return {"state": "unknown", "reason": "unresolved_ticker"}
        now = when or datetime.now()
        local_dt = self._to_market_time(now, instrument.timezone)
        open_time = self._parse_hhmm(instrument.regular_open)
        close_time = self._parse_hhmm(instrument.regular_close)
        is_weekday = local_dt.weekday() < 5
        in_regular = is_weekday and open_time <= local_dt.time() <= close_time
        state = "regular" if in_regular else ("closed" if is_weekday else "weekend")
        return {
            "state": state,
            "regular_session": in_regular,
            "market": instrument.market,
            "timezone": instrument.timezone,
            "local_time": local_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "regular_open": instrument.regular_open,
            "regular_close": instrument.regular_close,
            "holiday_calendar": "not_implemented",
        }

    def sector_benchmark_for_ticker(self, ticker: str, info: dict[str, Any] | None = None) -> str:
        instrument = self.resolve_instrument(ticker)
        if not instrument:
            return ""
        if instrument.market == "united_states":
            sector = str((info or {}).get("sector") or "").lower()
            industry = str((info or {}).get("industry") or "").lower()
            text = f"{sector} {industry}"
            if "semiconductor" in text:
                return "SOXX"
            mapping = [
                ("technology", "XLK"),
                ("financial", "XLF"),
                ("health", "XLV"),
                ("energy", "XLE"),
                ("consumer cyclical", "XLY"),
                ("consumer defensive", "XLP"),
                ("industrial", "XLI"),
                ("utility", "XLU"),
                ("real estate", "XLRE"),
                ("communication", "XLC"),
                ("basic material", "XLB"),
            ]
            for key, benchmark in mapping:
                if key in text:
                    return benchmark
        if instrument.market in {"korea_kospi", "korea_kosdaq", "korea"}:
            return os.getenv("KOREA_SECTOR_BENCHMARK_DEFAULT", "")
        return ""

    def resolve_instrument(self, ticker: str) -> MarketInstrument | None:
        requested = str(ticker or "").strip()
        if not requested:
            return None
        cache_key = requested.upper()
        if cache_key in self._resolution_cache:
            return self._resolution_cache[cache_key]
        candidates = self._ticker_candidates(requested)
        if not candidates:
            self._resolution_cache[cache_key] = None
            return None
        if yf is None:
            resolved = candidates[0]
            self._resolution_cache[cache_key] = resolved
            return resolved
        for candidate in candidates:
            if self._candidate_has_data(candidate.provider_ticker):
                self._resolution_cache[cache_key] = candidate
                return candidate
        self._resolution_cache[cache_key] = candidates[0]
        return candidates[0]

    def benchmark_for_ticker(self, ticker: str) -> str:
        instrument = self.resolve_instrument(ticker)
        return instrument.benchmark_ticker if instrument else "SPY"

    def _ticker_candidates(self, ticker: str) -> list[MarketInstrument]:
        raw = str(ticker or "").strip()
        if not raw:
            return []
        text = raw.upper().replace(" ", "")
        market_hint = ""
        symbol = text
        if ":" in text:
            market_hint, symbol = text.split(":", 1)
            market_hint = self._normalize_market_hint(market_hint)

        if self._looks_like_yahoo_symbol(symbol):
            return [self._instrument(raw, symbol, market_hint or self._market_from_suffix(symbol))]

        if market_hint:
            hinted = self._symbol_for_market(symbol, market_hint)
            resolved_market = self._market_from_suffix(hinted) if market_hint == "china" else market_hint
            return [self._instrument(raw, hinted, resolved_market)] if hinted else []

        if re.fullmatch(r"\d{6}", symbol):
            candidates = [
                self._instrument(raw, f"{symbol}.KS", "korea_kospi"),
                self._instrument(raw, f"{symbol}.KQ", "korea_kosdaq"),
            ]
            if symbol.startswith(("0", "3")):
                candidates.append(self._instrument(raw, f"{symbol}.SZ", "china_shenzhen"))
            if symbol.startswith(("5", "6", "9")):
                candidates.append(self._instrument(raw, f"{symbol}.SS", "china_shanghai"))
            return candidates
        if re.fullmatch(r"\d{4}", symbol):
            return [
                self._instrument(raw, f"{symbol}.HK", "hong_kong"),
                self._instrument(raw, f"{symbol}.T", "japan"),
            ]
        if re.fullmatch(r"\d{5}", symbol):
            return [self._instrument(raw, f"{symbol}.T", "japan")]
        if "-" in symbol or symbol.endswith("=X"):
            return [self._instrument(raw, symbol, "global")]
        return [self._instrument(raw, symbol, "united_states")]

    def _normalize_market_hint(self, hint: str) -> str:
        aliases = {
            "US": "united_states",
            "USA": "united_states",
            "NASDAQ": "united_states",
            "NYSE": "united_states",
            "AMEX": "united_states",
            "KR": "korea",
            "KOR": "korea",
            "KOREA": "korea",
            "KOSPI": "korea_kospi",
            "KOSDAQ": "korea_kosdaq",
            "JP": "japan",
            "JPN": "japan",
            "TOKYO": "japan",
            "HK": "hong_kong",
            "HKG": "hong_kong",
            "CN": "china",
            "SH": "china_shanghai",
            "SS": "china_shanghai",
            "SZ": "china_shenzhen",
            "UK": "united_kingdom",
            "LSE": "united_kingdom",
            "GB": "united_kingdom",
            "CA": "canada",
            "TSX": "canada_tsx",
            "TSXV": "canada_tsxv",
            "AU": "australia",
            "ASX": "australia",
            "IN": "india_nse",
            "NSE": "india_nse",
            "BSE": "india_bse",
            "DE": "germany",
            "XETRA": "germany",
            "FR": "france",
            "PA": "france",
            "SG": "singapore",
            "BR": "brazil",
            "MX": "mexico",
        }
        normalized = re.sub(r"[^A-Z0-9]", "", str(hint or "").upper())
        return aliases.get(normalized, normalized.lower())

    def _symbol_for_market(self, symbol: str, market: str) -> str:
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return ""
        if market == "united_states":
            return symbol
        if market in {"korea", "korea_kospi"}:
            return f"{symbol.zfill(6)}.KS" if symbol.isdigit() else f"{symbol}.KS"
        if market == "korea_kosdaq":
            return f"{symbol.zfill(6)}.KQ" if symbol.isdigit() else f"{symbol}.KQ"
        if market == "japan":
            return f"{symbol}.T"
        if market == "hong_kong":
            return f"{symbol.zfill(4)}.HK" if symbol.isdigit() else f"{symbol}.HK"
        if market == "china":
            return f"{symbol}.SZ" if symbol.startswith(("0", "3")) else f"{symbol}.SS"
        suffix_by_market = {
            "china_shanghai": ".SS",
            "china_shenzhen": ".SZ",
            "united_kingdom": ".L",
            "canada_tsx": ".TO",
            "canada": ".TO",
            "canada_tsxv": ".V",
            "australia": ".AX",
            "india_nse": ".NS",
            "india_bse": ".BO",
            "germany": ".DE",
            "france": ".PA",
            "singapore": ".SI",
            "brazil": ".SA",
            "mexico": ".MX",
        }
        suffix = suffix_by_market.get(market)
        return f"{symbol}{suffix}" if suffix else symbol

    def _looks_like_yahoo_symbol(self, symbol: str) -> bool:
        return bool(
            re.search(r"(\.[A-Z]{1,4}|-[A-Z]{3,5}|=X)$", symbol)
            or symbol.startswith("^")
        )

    def _market_from_suffix(self, symbol: str) -> str:
        suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
        return {
            "KS": "korea_kospi",
            "KQ": "korea_kosdaq",
            "T": "japan",
            "HK": "hong_kong",
            "SS": "china_shanghai",
            "SZ": "china_shenzhen",
            "L": "united_kingdom",
            "TO": "canada_tsx",
            "V": "canada_tsxv",
            "AX": "australia",
            "NS": "india_nse",
            "BO": "india_bse",
            "DE": "germany",
            "PA": "france",
            "SI": "singapore",
            "SA": "brazil",
            "MX": "mexico",
        }.get(suffix, "global")

    def _instrument(self, requested: str, provider_ticker: str, market: str) -> MarketInstrument:
        meta = {
            "united_states": ("United States", "US", "USD", "SPY", "America/New_York", "09:30", "16:00"),
            "korea": ("South Korea", "KR", "KRW", "069500.KS", "Asia/Seoul", "09:00", "15:30"),
            "korea_kospi": ("South Korea", "KOSPI", "KRW", "069500.KS", "Asia/Seoul", "09:00", "15:30"),
            "korea_kosdaq": ("South Korea", "KOSDAQ", "KRW", "229200.KS", "Asia/Seoul", "09:00", "15:30"),
            "japan": ("Japan", "TSE", "JPY", "1306.T", "Asia/Tokyo", "09:00", "15:30"),
            "hong_kong": ("Hong Kong", "HKEX", "HKD", "2800.HK", "Asia/Hong_Kong", "09:30", "16:00"),
            "china": ("China", "CN", "CNY", "510300.SS", "Asia/Shanghai", "09:30", "15:00"),
            "china_shanghai": ("China", "SSE", "CNY", "510300.SS", "Asia/Shanghai", "09:30", "15:00"),
            "china_shenzhen": ("China", "SZSE", "CNY", "159919.SZ", "Asia/Shanghai", "09:30", "15:00"),
            "united_kingdom": ("United Kingdom", "LSE", "GBp", "ISF.L", "Europe/London", "08:00", "16:30"),
            "canada": ("Canada", "TSX", "CAD", "XIU.TO", "America/Toronto", "09:30", "16:00"),
            "canada_tsx": ("Canada", "TSX", "CAD", "XIU.TO", "America/Toronto", "09:30", "16:00"),
            "canada_tsxv": ("Canada", "TSXV", "CAD", "XIU.TO", "America/Toronto", "09:30", "16:00"),
            "australia": ("Australia", "ASX", "AUD", "STW.AX", "Australia/Sydney", "10:00", "16:00"),
            "india_nse": ("India", "NSE", "INR", "NIFTYBEES.NS", "Asia/Kolkata", "09:15", "15:30"),
            "india_bse": ("India", "BSE", "INR", "NIFTYBEES.NS", "Asia/Kolkata", "09:15", "15:30"),
            "germany": ("Germany", "XETRA", "EUR", "EXS1.DE", "Europe/Berlin", "09:00", "17:30"),
            "france": ("France", "Euronext Paris", "EUR", "EWQ", "Europe/Paris", "09:00", "17:30"),
            "singapore": ("Singapore", "SGX", "SGD", "ES3.SI", "Asia/Singapore", "09:00", "17:00"),
            "brazil": ("Brazil", "B3", "BRL", "BOVA11.SA", "America/Sao_Paulo", "10:00", "17:00"),
            "mexico": ("Mexico", "BMV", "MXN", "EWW", "America/Mexico_City", "08:30", "15:00"),
            "global": ("Global", "GLOBAL", "", "ACWI", "UTC", "00:00", "23:59"),
            "unknown": ("", "", "", "SPY", "America/New_York", "09:30", "16:00"),
        }
        country, exchange, currency, benchmark, timezone, regular_open, regular_close = meta.get(market, meta["unknown"])
        return MarketInstrument(
            requested_ticker=requested,
            provider_ticker=provider_ticker,
            market=market,
            country=country,
            exchange_hint=exchange,
            currency_hint=currency,
            benchmark_ticker=benchmark,
            timezone=timezone,
            regular_open=regular_open,
            regular_close=regular_close,
        )

    def _candidate_has_data(self, provider_ticker: str) -> bool:
        try:
            obj = yf.Ticker(provider_ticker)
            info = obj.fast_info
            price = None
            try:
                price = info.get("lastPrice")
            except Exception:
                price = None
            if price and float(price) > 0:
                return True
            hist = obj.history(period="5d")
            return hist is not None and not getattr(hist, "empty", True)
        except Exception:
            return False

    def _get_yfinance_latest_quote(self, ticker: str) -> PriceQuote | None:
        if yf is None:
            return None
        instrument = self.resolve_instrument(ticker)
        if not instrument:
            return None
        normalized = instrument.provider_ticker
        try:
            ticker_obj = yf.Ticker(normalized)
            info = ticker_obj.info
            px = info.get("currentPrice") or info.get("regularMarketPrice")
            if not px or float(px) <= 0:
                try:
                    px = ticker_obj.fast_info.get("lastPrice")
                except Exception:
                    px = None
            if not px or float(px) <= 0:
                hist = ticker_obj.history(period="5d")
                if hist is not None and not getattr(hist, "empty", True):
                    px = hist.sort_index().iloc[-1].get("Close")
            if not px or float(px) <= 0:
                return None
            return PriceQuote(
                ticker=normalized,
                price=float(px),
                currency=str(info.get("currency") or info.get("financialCurrency") or instrument.currency_hint or ""),
                source="yfinance/Yahoo Finance",
                quality="reference_not_execution_grade",
                as_of=datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                detail={
                    "requested_ticker": instrument.requested_ticker,
                    "provider_ticker": instrument.provider_ticker,
                    "market": instrument.market,
                    "country": instrument.country,
                    "exchange_hint": instrument.exchange_hint,
                    "market_state": info.get("marketState"),
                    "exchange": info.get("exchange"),
                    "benchmark_ticker": instrument.benchmark_ticker,
                    "session": self.session_state(normalized),
                },
            )
        except Exception:
            return None

    def _parse_dt(self, value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    def _get_yfinance_historical_quote(self, ticker: str, when: datetime) -> PriceQuote | None:
        if yf is None:
            return None
        instrument = self.resolve_instrument(ticker)
        if not instrument:
            return None
        normalized = instrument.provider_ticker
        interval = "15m" if when.hour or when.minute else "1d"
        start = (when - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (when + timedelta(days=7)).strftime("%Y-%m-%d")
        cache_key = (normalized, interval, start, end)
        frame = self._history_cache.get(cache_key)
        if frame is None:
            try:
                frame = self.get_history_frame(normalized, start=when - timedelta(days=7), end=when + timedelta(days=7), interval=interval)
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
                currency=instrument.currency_hint,
                source="yfinance/Yahoo Finance",
                quality="historical_reference_not_execution_grade",
                as_of=chosen_ts.strftime('%Y-%m-%dT%H:%M:%S') if chosen_ts else "",
                detail={
                    "requested_ticker": instrument.requested_ticker,
                    "provider_ticker": instrument.provider_ticker,
                    "market": instrument.market,
                    "country": instrument.country,
                    "exchange_hint": instrument.exchange_hint,
                    "interval": interval,
                    "benchmark_ticker": instrument.benchmark_ticker,
                    "session": self.session_state(normalized, chosen_ts or when),
                },
            )
        except Exception:
            return None

    def _to_market_time(self, value: datetime, timezone: str) -> datetime:
        try:
            zone = ZoneInfo(timezone)
            if value.tzinfo is None:
                local_zone = datetime.now().astimezone().tzinfo
                value = value.replace(tzinfo=local_zone)
            return value.astimezone(zone).replace(tzinfo=None)
        except Exception:
            return value.replace(tzinfo=None)

    def _parse_hhmm(self, value: str):
        try:
            return datetime.strptime(value, "%H:%M").time()
        except Exception:
            return datetime.strptime("09:30", "%H:%M").time()
