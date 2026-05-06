from __future__ import annotations

import math
import os
import re
from datetime import datetime, timedelta
from typing import Any

from db_manager import DBManager


class RiskManager:
    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()
        self.default_position_pct = float(os.getenv("RISK_DEFAULT_POSITION_PCT", "0.05"))
        self.max_ticker_exposure_pct = float(os.getenv("RISK_MAX_TICKER_EXPOSURE_PCT", "0.20"))
        self.max_gross_exposure_pct = float(os.getenv("RISK_MAX_GROSS_EXPOSURE_PCT", "1.00"))
        self.max_open_positions = max(1, int(os.getenv("RISK_MAX_OPEN_POSITIONS", "8")))
        self.allow_shorts = os.getenv("ALLOW_PAPER_SHORTS", "false").strip().lower() in {"1", "true", "yes"}
        self.ticker_cooldown_min = max(0, int(os.getenv("RISK_TICKER_COOLDOWN_MIN", "30")))
        self.high_vol_threshold_pct = max(0.0, float(os.getenv("RISK_HIGH_VOL_THRESHOLD_PCT", "4.0")))
        self.high_vol_size_multiplier = min(1.0, max(0.1, float(os.getenv("RISK_HIGH_VOL_SIZE_MULTIPLIER", "0.5"))))

    def evaluate_event(
        self,
        *,
        event_id: str,
        recommendations: list[dict[str, Any]],
        prices: dict[str, float],
        market_contexts: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        account = self.db.get_paper_account_state()
        guardrail = self.db.get_guardrail_state()
        positions = {p["ticker"]: p for p in self.db.list_paper_positions()}
        equity = max(0.0, float(account.get("equity", 0.0)))
        buying_power = max(0.0, float(account.get("buying_power", 0.0)))
        simulated_qty = {
            ticker: float(pos.get("qty", 0.0) or 0.0)
            for ticker, pos in positions.items()
        }
        planned_gross = sum(
            abs(float(pos.get("qty", 0.0) or 0.0) * float(pos.get("market_price", pos.get("avg_price", 0.0)) or 0.0))
            for pos in positions.values()
        )

        if guardrail.get("daily_loss_limit", 0.0) > 0:
            running_pnl = float(account.get("realized_pnl", 0.0)) + float(account.get("unrealized_pnl", 0.0))
            if running_pnl <= -abs(float(guardrail["daily_loss_limit"])):
                return False, "❌ 리스크 차단: 손실 한도 초과", []

        if len([qty for qty in simulated_qty.values() if abs(qty) > 1e-9]) >= self.max_open_positions:
            unseen = {str(r.get("ticker", "")).upper().strip() for r in recommendations if str(r.get("ticker", "")).upper().strip() not in positions}
            if unseen:
                return False, "❌ 리스크 차단: 최대 보유 종목 수 초과", []

        plan: list[dict[str, Any]] = []
        planned_cash = buying_power

        for rec in recommendations:
            ticker = str(rec.get("ticker", "")).upper().strip()
            side = str(rec.get("side", "BUY")).upper().strip()
            price = float(prices.get(ticker, 0.0) or 0.0)
            if not ticker or price <= 0:
                return False, f"❌ 리스크 차단: 가격 정보 부족 `{ticker}`", []
            current_qty = float(simulated_qty.get(ticker, 0.0) or 0.0)
            if side == "SELL" and not self.allow_shorts and current_qty <= 0:
                return False, f"❌ 리스크 차단: 숏 비활성화 상태에서 `{ticker}` 매도 불가", []
            if self._is_in_cooldown(ticker):
                return False, f"❌ 리스크 차단: `{ticker}` 최근 체결 후 cooldown 중", []

            market_ctx = (market_contexts or {}).get(ticker, {})
            qty = self._resolve_qty(
                rec,
                price=price,
                equity=equity,
                buying_power=planned_cash,
                market_context=market_ctx,
            )
            if qty <= 0:
                return False, f"❌ 리스크 차단: `{ticker}` 수량 계산 실패", []

            signed_delta = qty if side == "BUY" else -qty
            projected_qty = current_qty + signed_delta
            if projected_qty < -1e-9 and not self.allow_shorts:
                return False, f"❌ 리스크 차단: 숏 비활성화 상태에서 `{ticker}` 매도 불가", []

            notional = qty * price
            current_abs_value = abs(current_qty * price)
            projected_abs_value = abs(projected_qty * price)
            if equity > 0 and projected_abs_value > equity * self.max_ticker_exposure_pct + 1e-9:
                if side == "SELL" and current_qty > 0:
                    qty = min(qty, current_qty)
                else:
                    capped_qty = self._floor_qty((equity * self.max_ticker_exposure_pct) / price)
                    if capped_qty <= 0:
                        return False, f"❌ 리스크 차단: `{ticker}` 종목 노출 한도 초과", []
                    qty = capped_qty
                signed_delta = qty if side == "BUY" else -qty
                projected_qty = current_qty + signed_delta
                notional = qty * price
                projected_abs_value = abs(projected_qty * price)

            projected_gross = planned_gross - current_abs_value + projected_abs_value
            if equity > 0 and projected_gross > equity * self.max_gross_exposure_pct + 1e-9:
                allowed_abs_value = max(0.0, equity * self.max_gross_exposure_pct - (planned_gross - current_abs_value))
                if side == "SELL" and current_qty > 0:
                    qty = min(qty, current_qty)
                else:
                    qty = self._floor_qty(allowed_abs_value / price)
                if qty <= 0 and projected_abs_value > current_abs_value:
                    return False, "❌ 리스크 차단: 총 익스포저 한도 초과", []
                signed_delta = qty if side == "BUY" else -qty
                projected_qty = current_qty + signed_delta
                notional = qty * price
                projected_abs_value = abs(projected_qty * price)
                projected_gross = planned_gross - current_abs_value + projected_abs_value

            if side == "BUY" and notional > planned_cash + 1e-9:
                affordable_qty = self._floor_qty(planned_cash / price)
                if affordable_qty <= 0:
                    return False, f"❌ 리스크 차단: `{ticker}` 매수 가능 현금 부족", []
                qty = affordable_qty
                signed_delta = qty if side == "BUY" else -qty
                projected_qty = current_qty + signed_delta
                notional = qty * price
                projected_abs_value = abs(projected_qty * price)
                projected_gross = planned_gross - current_abs_value + projected_abs_value

            if qty <= 0:
                return False, f"❌ 리스크 차단: `{ticker}` 실행 수량 0", []

            if side == "BUY":
                planned_cash -= notional
            else:
                planned_cash += notional
            simulated_qty[ticker] = projected_qty
            planned_gross = projected_gross
            plan.append(
                {
                    "event_id": event_id,
                    "ticker": ticker,
                    "side": side,
                    "qty": qty,
                    "price": price,
                    "notional": notional,
                    "size_rule": rec.get("size_rule", ""),
                    "confidence": float(rec.get("confidence", 0.0) or 0.0),
                    "rationale": rec.get("rationale", ""),
                    "volatility_pct": float(market_ctx.get("volatility_pct", 0.0) or 0.0),
                }
            )
        return True, "ok", plan

    def _resolve_qty(
        self,
        rec: dict[str, Any],
        *,
        price: float,
        equity: float,
        buying_power: float,
        market_context: dict[str, Any] | None = None,
    ) -> float:
        size_rule = str(rec.get("size_rule", "")).strip().lower()
        confidence = min(1.0, max(0.0, float(rec.get("confidence", 0.0) or 0.0)))
        volatility_pct = float((market_context or {}).get("volatility_pct", 0.0) or 0.0)
        vol_multiplier = self.high_vol_size_multiplier if volatility_pct >= self.high_vol_threshold_pct > 0 else 1.0
        if not size_rule:
            budget_pct = self.default_position_pct * (0.7 + (confidence * 0.6)) * vol_multiplier
            return self._floor_qty((equity * budget_pct) / price)

        m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", size_rule)
        if m_pct:
            pct = float(m_pct.group(1)) / 100.0
            return self._floor_qty((equity * pct) / price)

        m_dollar = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*(usd|dollars?)?", size_rule)
        if "$" in size_rule or "usd" in size_rule or "dollar" in size_rule:
            notional = float(m_dollar.group(1)) if m_dollar else 0.0
            return self._floor_qty(notional / price)

        m_unit = re.search(r"(\d+(?:\.\d+)?)", size_rule)
        if m_unit:
            raw_qty = float(m_unit.group(1))
            if "unit" in size_rule or "share" in size_rule or "주" in size_rule:
                return self._floor_qty(raw_qty)

        budget_pct = self.default_position_pct * (0.7 + (confidence * 0.6)) * vol_multiplier
        budget = min(buying_power, equity * budget_pct if equity > 0 else buying_power)
        return self._floor_qty(budget / price)

    def _floor_qty(self, qty: float) -> float:
        qty = float(qty or 0.0)
        if qty <= 0:
            return 0.0
        if qty >= 1:
            return float(math.floor(qty))
        return round(qty, 4)

    def _is_in_cooldown(self, ticker: str) -> bool:
        if self.ticker_cooldown_min <= 0:
            return False
        rows = self.db.list_order_executions(ticker=ticker, limit=1)
        if not rows:
            return False
        ts = rows[0].get("filled_at") or rows[0].get("submitted_at")
        if not ts:
            return False
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
        except Exception:
            return False
        return (datetime.now() - dt) < timedelta(minutes=self.ticker_cooldown_min)
