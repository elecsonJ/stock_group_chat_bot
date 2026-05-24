from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from db_manager import DBManager
from market_data_provider import MarketDataProvider, PriceQuote


class MarketReactionAnalyzer:
    """Capture price reaction windows around signal/news events."""

    WINDOWS = {
        "pre_60m": timedelta(minutes=-60),
        "pre_30m": timedelta(minutes=-30),
        "pre_5m": timedelta(minutes=-5),
        "event": timedelta(minutes=0),
        "post_5m": timedelta(minutes=5),
        "post_30m": timedelta(minutes=30),
        "post_60m": timedelta(minutes=60),
        "post_1d": timedelta(days=1),
    }

    def __init__(self, db: DBManager | None = None, market_data: MarketDataProvider | None = None):
        self.db = db or DBManager()
        self.market_data = market_data or MarketDataProvider()

    def capture_for_signal_event(
        self,
        event_id: str,
        *,
        benchmark_ticker: str = "SPY",
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        event = self.db.get_signal_event(event_id)
        if not event:
            return []
        tickers = [str(t).upper().strip() for t in event.get("related_tickers", []) if str(t).strip()]
        event_time = self._parse_event_time(event)
        if not tickers or event_time is None:
            return []
        rows = []
        for ticker in tickers[:8]:
            resolved_benchmark = benchmark_ticker
            if benchmark_ticker == "SPY":
                resolved_benchmark = self.market_data.benchmark_for_ticker(ticker)
            row = self.capture(
                event_id=event.get("event_id", event_id),
                event_key=event.get("event_key", ""),
                ticker=ticker,
                event_time=event_time,
                benchmark_ticker=resolved_benchmark,
            )
            rows.append(row)
            if persist:
                self.db.save_market_reaction_snapshot(row)
        return rows

    def capture(
        self,
        *,
        event_id: str,
        event_key: str = "",
        ticker: str,
        event_time: datetime,
        benchmark_ticker: str = "SPY",
        sector_ticker: str = "",
    ) -> dict[str, Any]:
        ticker = str(ticker or "").upper().strip()
        if benchmark_ticker == "SPY":
            benchmark_ticker = self.market_data.benchmark_for_ticker(ticker)
        if not sector_ticker:
            sector_fn = getattr(self.market_data, "sector_benchmark_for_ticker", None)
            if callable(sector_fn):
                try:
                    sector_ticker = str(sector_fn(ticker) or "")
                except Exception:
                    sector_ticker = ""
        quotes = {
            key: self.market_data.get_historical_price(ticker, event_time + offset)
            for key, offset in self.WINDOWS.items()
        }
        benchmark_quotes = {
            key: self.market_data.get_historical_price(benchmark_ticker, event_time + offset)
            for key, offset in {"event": timedelta(minutes=0), "post_60m": timedelta(minutes=60)}.items()
        }
        sector_quotes = {}
        if sector_ticker:
            sector_quotes = {
                key: self.market_data.get_historical_price(sector_ticker, event_time + offset)
                for key, offset in {"event": timedelta(minutes=0), "post_60m": timedelta(minutes=60)}.items()
            }

        def price(key: str) -> float:
            q = quotes.get(key)
            return float(q.price) if q else 0.0

        event_price = price("event")
        pre_60 = price("pre_60m")
        pre_5 = price("pre_5m")
        post_60 = price("post_60m")
        post_1d = price("post_1d")
        pre_news_move = self._return_pct(pre_60, pre_5)
        post_60_move = self._return_pct(event_price, post_60)
        post_1d_move = self._return_pct(event_price, post_1d)
        benchmark_post_60 = self._return_pct(
            self._quote_price(benchmark_quotes.get("event")),
            self._quote_price(benchmark_quotes.get("post_60m")),
        )
        sector_post_60 = self._return_pct(
            self._quote_price(sector_quotes.get("event")),
            self._quote_price(sector_quotes.get("post_60m")),
        ) if sector_quotes else 0.0
        relative_post_60 = post_60_move - benchmark_post_60
        relative_sector_post_60 = post_60_move - sector_post_60
        missing = [key for key, quote in quotes.items() if quote is None]
        quality = self._quality_state(missing, quotes)
        volume_zscore = self._volume_zscore(ticker, event_time)
        already_priced = abs(pre_news_move) >= 2.0 and abs(post_60_move) < abs(pre_news_move) * 0.35

        return {
            "event_id": event_id,
            "event_key": event_key,
            "ticker": ticker,
            "event_time": event_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "captured_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "benchmark_ticker": benchmark_ticker,
            "sector_ticker": sector_ticker,
            "pre_60m_price": pre_60,
            "pre_30m_price": price("pre_30m"),
            "pre_5m_price": pre_5,
            "event_price": event_price,
            "post_5m_price": price("post_5m"),
            "post_30m_price": price("post_30m"),
            "post_60m_price": post_60,
            "post_1d_price": post_1d,
            "pre_news_move_pct": round(pre_news_move, 4),
            "post_60m_move_pct": round(post_60_move, 4),
            "post_1d_move_pct": round(post_1d_move, 4),
            "benchmark_post_60m_pct": round(benchmark_post_60, 4),
            "relative_post_60m_pct": round(relative_post_60, 4),
            "volume_zscore": round(volume_zscore, 4),
            "already_priced_in": already_priced,
            "market_data_quality": quality,
            "detail_json": {
                "missing_windows": missing,
                "quote_as_of": {key: quote.as_of for key, quote in quotes.items() if quote},
                "benchmark_missing": [key for key, quote in benchmark_quotes.items() if quote is None],
                "sector_post_60m_pct": round(sector_post_60, 4),
                "relative_sector_post_60m_pct": round(relative_sector_post_60, 4),
                "sector_missing": [key for key, quote in sector_quotes.items() if quote is None],
                "reaction_score_adjustments": self.score_adjustment(
                    {
                        "pre_news_move_pct": pre_news_move,
                        "post_60m_move_pct": post_60_move,
                        "relative_post_60m_pct": relative_post_60,
                        "volume_zscore": volume_zscore,
                        "already_priced_in": already_priced,
                        "market_data_quality": quality,
                    }
                ),
            },
        }

    def score_adjustment(self, row: dict[str, Any], direction: str = "neutral") -> dict[str, Any]:
        quality = str(row.get("market_data_quality") or "").lower()
        if quality in {"missing", "partial"}:
            return {"score_delta": 0.0, "confidence_delta": -0.05, "reason": "market_reaction_incomplete"}
        pre_move = float(row.get("pre_news_move_pct", 0.0) or 0.0)
        post_move = float(row.get("post_60m_move_pct", 0.0) or 0.0)
        relative_move = float(row.get("relative_post_60m_pct", 0.0) or 0.0)
        volume_z = float(row.get("volume_zscore", 0.0) or 0.0)
        already_priced = bool(row.get("already_priced_in", False))
        direction = str(direction or "neutral").lower()

        directional_move = relative_move
        if direction == "bearish":
            directional_move = -relative_move
        elif direction == "neutral":
            directional_move = abs(relative_move) * 0.35

        score_delta = 0.0
        reasons = []
        if already_priced:
            score_delta -= 10.0
            reasons.append("pre_news_move_suggests_already_priced")
        if directional_move >= 1.0:
            score_delta += min(8.0, directional_move * 2.0)
            reasons.append("post_news_move_confirms_direction")
        elif directional_move <= -1.0:
            score_delta += max(-8.0, directional_move * 2.0)
            reasons.append("post_news_move_contradicts_direction")
        if volume_z >= 2.0:
            score_delta += 4.0
            reasons.append("abnormal_volume_confirmation")
        elif 0 < volume_z < 0.5 and abs(post_move) >= 1.5:
            score_delta -= 2.0
            reasons.append("price_move_without_volume_confirmation")
        if abs(pre_move) >= 4.0 and abs(post_move) < 1.0:
            score_delta -= 4.0
            reasons.append("reaction_mostly_before_detection")

        return {
            "score_delta": round(max(-15.0, min(12.0, score_delta)), 2),
            "confidence_delta": 0.03 if score_delta > 3 else (-0.04 if score_delta < -3 else 0.0),
            "reason": ",".join(reasons) or "market_reaction_neutral",
        }

    def _parse_event_time(self, event: dict[str, Any]) -> datetime | None:
        for key in ("detected_at", "last_verified_at", "date"):
            value = event.get(key)
            if not value:
                continue
            try:
                text = str(value).replace("Z", "+00:00")
                if len(text) == 10:
                    text += "T00:00:00"
                return datetime.fromisoformat(text).replace(tzinfo=None)
            except Exception:
                continue
        return None

    def _return_pct(self, start: float, end: float) -> float:
        if start <= 0 or end <= 0:
            return 0.0
        return ((end - start) / start) * 100.0

    def _quote_price(self, quote: PriceQuote | None) -> float:
        return float(quote.price) if quote else 0.0

    def _volume_zscore(self, ticker: str, event_time: datetime) -> float:
        history_fn = getattr(self.market_data, "get_history_frame", None)
        if not callable(history_fn):
            return 0.0
        try:
            frame = history_fn(
                ticker,
                start=event_time - timedelta(days=90),
                end=event_time + timedelta(days=2),
                interval="1d",
            )
        except Exception:
            return 0.0
        if frame is None or getattr(frame, "empty", True) or "Volume" not in getattr(frame, "columns", []):
            return 0.0
        try:
            indexed = frame.sort_index()
            volumes = [float(v or 0.0) for v in indexed["Volume"].dropna().tolist() if float(v or 0.0) > 0]
            if len(volumes) < 20:
                return 0.0
            event_volume = volumes[-1]
            baseline = volumes[-61:-1] if len(volumes) >= 61 else volumes[:-1]
            if len(baseline) < 10:
                return 0.0
            mean = sum(baseline) / len(baseline)
            variance = sum((v - mean) ** 2 for v in baseline) / len(baseline)
            std = variance ** 0.5
            if std <= 0:
                return 0.0
            return (event_volume - mean) / std
        except Exception:
            return 0.0

    def _quality_state(self, missing: list[str], quotes: dict[str, PriceQuote | None]) -> str:
        if not quotes or len(missing) == len(quotes):
            return "missing"
        critical = {"event", "post_60m"}
        if critical & set(missing):
            return "partial"
        if missing:
            return "reference_partial"
        return "reference"
