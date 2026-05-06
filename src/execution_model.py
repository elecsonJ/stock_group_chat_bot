from __future__ import annotations

import os


class ExecutionModel:
    """
    페이퍼 체결에 최소한의 현실성을 주기 위한 execution model.
    """

    def __init__(self):
        self.base_slippage_bps = max(0.0, float(os.getenv("PAPER_SLIPPAGE_BPS", "2")))
        self.base_spread_bps = max(0.0, float(os.getenv("PAPER_SPREAD_BPS", "1")))
        self.immediate_urgency_bps = max(0.0, float(os.getenv("PAPER_IMMEDIATE_URGENCY_BPS", "2")))
        self.high_vol_multiplier = max(1.0, float(os.getenv("PAPER_HIGH_VOL_MULTIPLIER", "1.5")))
        self.high_vol_threshold_pct = max(0.0, float(os.getenv("RISK_HIGH_VOL_THRESHOLD_PCT", "4.0")))

    def apply_fill_price(
        self,
        *,
        reference_price: float,
        side: str,
        urgency: str = "",
        volatility_pct: float | None = None,
    ) -> dict[str, float]:
        ref = float(reference_price or 0.0)
        if ref <= 0:
            raise ValueError("reference_price must be positive")

        total_bps = self.base_slippage_bps + self.base_spread_bps
        if str(urgency or "").strip().lower() == "immediate":
            total_bps += self.immediate_urgency_bps
        if volatility_pct is not None and float(volatility_pct) >= self.high_vol_threshold_pct:
            total_bps *= self.high_vol_multiplier

        impact = ref * (total_bps / 10000.0)
        normalized_side = str(side or "BUY").upper().strip()
        if normalized_side == "SELL":
            fill_price = max(0.0001, ref - impact)
        else:
            fill_price = ref + impact
        return {
            "reference_price": ref,
            "fill_price": round(fill_price, 6),
            "slippage_bps": round(total_bps, 4),
            "price_impact": round(fill_price - ref, 6),
        }
