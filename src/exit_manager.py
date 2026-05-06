from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

PriceGetter = Callable[[str, datetime], float | None]


class ExitManager:
    def __init__(self, price_getter: PriceGetter):
        self.price_getter = price_getter

    def resolve_exit(
        self,
        *,
        ticker: str,
        side: str,
        entry_dt: datetime,
        entry_price: float,
        horizon_end_dt: datetime,
        stop_rule: str = "",
        ttl_sec: int = 0,
        check_interval: timedelta = timedelta(minutes=15),
    ) -> dict[str, object]:
        final_end = horizon_end_dt
        if ttl_sec > 0:
            ttl_end = entry_dt + timedelta(seconds=int(ttl_sec))
            if ttl_end < final_end:
                final_end = ttl_end

        stop_pct = self._parse_stop_pct(stop_rule)
        normalized_side = str(side or "BUY").upper().strip()
        current = entry_dt + check_interval

        if stop_pct > 0:
            while current <= final_end:
                px = self.price_getter(ticker, current)
                if px and self._stop_triggered(normalized_side, float(entry_price), float(px), stop_pct):
                    return {
                        "exit_price": float(px),
                        "exit_time": current.strftime('%Y-%m-%dT%H:%M:%S'),
                        "exit_reason": "stop_loss",
                    }
                current += check_interval

        final_price = self.price_getter(ticker, final_end)
        if final_price and final_price > 0:
            return {
                "exit_price": float(final_price),
                "exit_time": final_end.strftime('%Y-%m-%dT%H:%M:%S'),
                "exit_reason": "time_exit",
            }
        return {
            "exit_price": float(entry_price),
            "exit_time": final_end.strftime('%Y-%m-%dT%H:%M:%S'),
            "exit_reason": "fallback_entry",
        }

    def _parse_stop_pct(self, stop_rule: str) -> float:
        text = str(stop_rule or "").lower()
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
        if not m:
            return 0.0
        return abs(float(m.group(1)))

    def _stop_triggered(self, side: str, entry_price: float, current_price: float, stop_pct: float) -> bool:
        if entry_price <= 0 or current_price <= 0 or stop_pct <= 0:
            return False
        threshold = stop_pct / 100.0
        if side == "SELL":
            return current_price >= entry_price * (1.0 + threshold)
        return current_price <= entry_price * (1.0 - threshold)
