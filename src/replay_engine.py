from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

from db_manager import DBManager
from exit_manager import ExitManager
from market_data_provider import MarketDataProvider
from performance_tracker import PerformanceTracker

PriceProvider = Callable[[str, datetime], float | None]


class ReplayEngine:
    DEFAULT_HORIZONS = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
    }

    def __init__(
        self,
        db: DBManager | None = None,
        performance: PerformanceTracker | None = None,
        price_provider: PriceProvider | None = None,
        benchmark_ticker: str = "SPY",
    ):
        self.db = db or DBManager()
        self.performance = performance or PerformanceTracker(self.db)
        self.price_provider = price_provider
        self.benchmark_ticker = benchmark_ticker
        self.market_data = MarketDataProvider()
        self.exit_manager = ExitManager(self._get_price)

    def replay_event(self, event_id: str, horizons: dict[str, timedelta] | None = None) -> list[dict[str, Any]]:
        entries, signal_event = self._load_entries(event_id)
        if not entries:
            return []

        results: list[dict[str, Any]] = []
        active_horizons = horizons or self.DEFAULT_HORIZONS
        for entry in entries:
            entry_dt = self._parse_dt(entry.get("entry_time"))
            if entry_dt is None:
                continue
            benchmark_entry = self._get_price(self.benchmark_ticker, entry_dt)
            for horizon, delta in active_horizons.items():
                horizon_end = entry_dt + delta
                exit_info = self.exit_manager.resolve_exit(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    entry_dt=entry_dt,
                    entry_price=float(entry["entry_price"]),
                    horizon_end_dt=horizon_end,
                    stop_rule=str(entry.get("stop_rule", "")),
                    ttl_sec=int(entry.get("ttl_sec", 0) or 0),
                )
                exit_price = float(exit_info.get("exit_price", 0.0) or 0.0)
                if exit_price <= 0:
                    continue
                exit_dt = self._parse_dt(exit_info.get("exit_time")) or horizon_end
                benchmark_exit = self._get_price(self.benchmark_ticker, exit_dt) if benchmark_entry else None
                result = self.performance.record_measurement(
                    event_id=event_id,
                    ticker=entry["ticker"],
                    horizon=horizon,
                    entry_price=float(entry["entry_price"]),
                    exit_price=exit_price,
                    side=entry["side"],
                    benchmark_ticker=self.benchmark_ticker if benchmark_entry and benchmark_exit else None,
                    benchmark_entry_price=benchmark_entry,
                    benchmark_exit_price=benchmark_exit,
                    source="replay",
                    detail_json={
                        "side": entry["side"],
                        "entry_time": entry_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                        "measured_at": exit_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                        "origin": entry["origin"],
                        "exit_reason": exit_info.get("exit_reason", "time_exit"),
                        "stop_rule": entry.get("stop_rule", ""),
                        "ttl_sec": int(entry.get("ttl_sec", 0) or 0),
                    },
                )
                self.performance.record_attributions(signal_event=signal_event, measurement=result, source="replay")
                results.append(result)
        return results

    def replay_recent(
        self,
        limit: int = 50,
        statuses: tuple[str, ...] = ("executed", "pending_approval", "monitor_only"),
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for signal in self.db.list_recent_signal_events(limit=limit):
            if signal.get("status") not in statuses:
                continue
            results.extend(self.replay_event(str(signal.get("event_id", ""))))
        return results

    def replay_split(
        self,
        *,
        split_date: str,
        run_name: str = "default_split",
        train_start: str | None = None,
        eval_end: str | None = None,
        horizon: str = "1d",
        statuses: list[str] | None = None,
        limit: int = 500,
        starting_equity: float = 100000.0,
    ) -> dict[str, Any]:
        statuses = statuses or ["executed", "pending_approval", "monitor_only"]
        events = self.db.list_signal_events_between(
            start_date=train_start,
            end_date=eval_end,
            limit=limit,
            statuses=statuses,
        )
        train_events = [e for e in events if str(e.get("date", "")) < split_date]
        test_events = [e for e in events if str(e.get("date", "")) >= split_date]
        horizon_delta = self.DEFAULT_HORIZONS.get(horizon, timedelta(days=1))

        train_rows = self._replay_signal_rows(train_events, {horizon: horizon_delta})
        test_rows = self._replay_signal_rows(test_events, {horizon: horizon_delta})

        train_summary = self.performance.save_run_summary(
            run_name=run_name,
            split_label="train",
            horizon=horizon,
            rows=train_rows,
            window_start=train_start,
            window_end=(split_date if train_events else train_start),
            signal_count=len(train_events),
            starting_equity=starting_equity,
        )
        test_summary = self.performance.save_run_summary(
            run_name=run_name,
            split_label="test",
            horizon=horizon,
            rows=test_rows,
            window_start=split_date,
            window_end=(eval_end if eval_end else (test_events[-1].get("date") if test_events else split_date)),
            signal_count=len(test_events),
            starting_equity=starting_equity,
        )
        return {
            "run_name": run_name,
            "split_date": split_date,
            "train_summary": train_summary,
            "test_summary": test_summary,
            "train_measurements": len(train_rows),
            "test_measurements": len(test_rows),
        }

    def _replay_signal_rows(self, events: list[dict[str, Any]], horizons: dict[str, timedelta]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in events:
            rows.extend(self.replay_event(str(event.get("event_id", "")), horizons=horizons))
        return rows

    def _load_entries(self, event_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        signal = self.db.get_signal_event(event_id)
        rows = self.db.list_order_executions(event_id=event_id, limit=20)
        if rows:
            return (
                [
                    {
                        "ticker": str(row.get("ticker", "")).upper().strip(),
                        "side": str(row.get("side", "BUY")).upper().strip(),
                        "entry_price": float(row.get("fill_price", 0.0) or 0.0),
                        "entry_time": row.get("filled_at") or row.get("submitted_at"),
                        "origin": "execution",
                        "stop_rule": str((row.get("detail_json") or {}).get("stop_rule", "")),
                        "ttl_sec": int((row.get("detail_json") or {}).get("ttl_sec", 0) or 0),
                    }
                    for row in rows
                    if float(row.get("fill_price", 0.0) or 0.0) > 0
                ],
                signal,
            )

        recs = self.db.get_recommendations(event_id)
        if not signal or not recs:
            return [], signal

        entry_dt = self._parse_dt(signal.get("detected_at")) or self._parse_dt(signal.get("date"))
        if entry_dt is None:
            return [], signal
        out = []
        for rec in recs:
            ticker = str(rec.get("ticker", "")).upper().strip()
            entry_price = self._get_price(ticker, entry_dt)
            if not entry_price or entry_price <= 0:
                continue
            out.append(
                {
                    "ticker": ticker,
                    "side": str(rec.get("side", "BUY")).upper().strip(),
                    "entry_price": entry_price,
                    "entry_time": entry_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                    "origin": "signal",
                    "stop_rule": str(rec.get("stop_rule", "")),
                    "ttl_sec": int(rec.get("ttl_sec", 0) or 0),
                }
            )
        return out, signal

    def _get_price(self, ticker: str, when: datetime) -> float | None:
        normalized = str(ticker or "").upper().strip()
        if self.price_provider:
            try:
                price = self.price_provider(normalized, when)
                if price is not None:
                    return float(price)
            except Exception:
                pass
        quote = self.market_data.get_historical_price(normalized, when)
        return float(quote.price) if quote else None

    def _get_price_from_yfinance(self, ticker: str, when: datetime) -> float | None:
        if yf is None or not ticker:
            return None
        interval = "15m" if when.hour or when.minute else "1d"
        start = (when - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (when + timedelta(days=7)).strftime("%Y-%m-%d")
        cache_key = (ticker, interval, start, end)
        frame = self._history_cache.get(cache_key)
        if frame is None:
            try:
                frame = yf.Ticker(ticker).history(start=start, end=end, interval=interval)
            except Exception:
                frame = None
            self._history_cache[cache_key] = frame
        if frame is None or getattr(frame, "empty", True):
            return None
        try:
            indexed = frame.sort_index()
            target = when.replace(tzinfo=None)
            chosen = None
            for idx, row in indexed.iterrows():
                ts = idx.to_pydatetime().replace(tzinfo=None)
                if ts >= target:
                    chosen = row
                    break
            if chosen is None:
                chosen = indexed.iloc[-1]
            close_value = chosen.get("Close")
            return float(close_value) if close_value is not None else None
        except Exception:
            return None

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                return dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception:
            try:
                return datetime.strptime(str(value), "%Y-%m-%d")
            except Exception:
                return None
