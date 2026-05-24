from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import uuid4

from broker_adapter import BrokerAdapter
from db_manager import DBManager
from execution_model import ExecutionModel


class PaperBroker(BrokerAdapter):
    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()
        self.allow_shorts = os.getenv("ALLOW_PAPER_SHORTS", "false").strip().lower() in {"1", "true", "yes"}
        self.commission_per_order = max(0.0, float(os.getenv("PAPER_COMMISSION_PER_ORDER", "0")))
        self.execution_model = ExecutionModel()

    def get_account_state(self) -> dict[str, Any]:
        return self.db.get_paper_account_state()

    def get_positions(self) -> list[dict[str, Any]]:
        return self.db.list_paper_positions()

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self.db.list_paper_orders(limit=100, statuses=["submitted", "pending", "partially_filled"])

    def get_fills(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.list_paper_fills(limit=limit)

    def cancel_order(self, client_order_id: str) -> dict[str, Any]:
        order = self.db.get_paper_order(client_order_id)
        if not order:
            return {"ok": False, "client_order_id": client_order_id, "status": "not_found"}
        if order.get("status") == "filled":
            return {"ok": False, "client_order_id": client_order_id, "status": "already_filled"}
        order["status"] = "canceled"
        order["notes"] = (order.get("notes") or "") + " canceled"
        self.db.save_paper_order(order)
        return {"ok": True, "client_order_id": client_order_id, "status": "canceled"}

    def refresh_market_prices(self, price_map: dict[str, float]) -> dict[str, Any]:
        positions = self.db.list_paper_positions()
        total_unrealized = 0.0
        total_market_value = 0.0
        for pos in positions:
            ticker = str(pos.get("ticker", "")).upper().strip()
            if ticker in price_map and float(price_map[ticker] or 0.0) > 0:
                market_price = float(price_map[ticker])
            else:
                market_price = float(pos.get("market_price", 0.0) or 0.0)
            qty = float(pos.get("qty", 0.0))
            avg_price = float(pos.get("avg_price", 0.0))
            market_value = qty * market_price
            unrealized = 0.0
            if qty > 0:
                unrealized = (market_price - avg_price) * qty
            elif qty < 0:
                unrealized = (avg_price - market_price) * abs(qty)
            total_unrealized += unrealized
            total_market_value += market_value
            self.db.upsert_paper_position(
                {
                    "ticker": ticker,
                    "qty": qty,
                    "avg_price": avg_price,
                    "market_price": market_price,
                    "market_value": market_value,
                    "realized_pnl": float(pos.get("realized_pnl", 0.0)),
                    "unrealized_pnl": unrealized,
                }
            )

        account = self.db.get_paper_account_state()
        cash_balance = float(account.get("cash_balance", 0.0))
        equity = cash_balance + total_market_value
        buying_power = cash_balance if not self.allow_shorts else max(cash_balance, equity)
        self.db.update_paper_account_state(
            {
                "equity": equity,
                "buying_power": buying_power,
                "unrealized_pnl": total_unrealized,
            }
        )
        return self.db.get_paper_account_state()

    def submit_market_order(
        self,
        *,
        event_id: str,
        ticker: str,
        side: str,
        qty: float,
        fill_price: float,
        order_type: str = "paper_market",
        notes: str = "",
        detail_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_ticker = str(ticker or "").upper().strip()
        normalized_side = str(side or "BUY").upper().strip()
        qty = float(qty or 0.0)
        fill_price = float(fill_price or 0.0)
        if not normalized_ticker:
            raise ValueError("ticker is required")
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported side: {normalized_side}")
        if qty <= 0 or fill_price <= 0:
            raise ValueError("qty and fill_price must be positive")

        detail_json = detail_json or {}
        execution_adjustment = self.execution_model.apply_fill_price(
            reference_price=fill_price,
            side=normalized_side,
            urgency=str(detail_json.get("urgency", "")),
            volatility_pct=float(detail_json.get("volatility_pct", 0.0) or 0.0) if detail_json.get("volatility_pct") is not None else None,
        )
        fill_price = float(execution_adjustment["fill_price"])

        account = self.db.get_paper_account_state()
        existing = self.db.get_paper_position(normalized_ticker) or {
            "ticker": normalized_ticker,
            "qty": 0.0,
            "avg_price": 0.0,
            "market_price": 0.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
        }

        signed_delta = qty if normalized_side == "BUY" else -qty
        if existing["qty"] <= 0 and normalized_side == "SELL" and not self.allow_shorts:
            raise ValueError(f"short selling disabled: {normalized_ticker}")

        commission = self.commission_per_order
        cash_balance = float(account.get("cash_balance", 0.0))
        gross_notional = qty * fill_price
        cash_after = cash_balance - gross_notional - commission if normalized_side == "BUY" else cash_balance + gross_notional - commission
        if normalized_side == "BUY" and cash_after < -1e-9:
            raise ValueError(f"insufficient cash for {normalized_ticker}")

        position_update = self._apply_fill_to_position(existing, signed_delta=signed_delta, fill_price=fill_price)
        client_order_id = f"PO-{event_id}-{normalized_ticker}-{uuid4().hex[:10].upper()}"
        broker_order_id = f"PAPER-{uuid4().hex[:12].upper()}"
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        merged_detail = {
            **detail_json,
            "commission": commission,
            "gross_notional": gross_notional,
            "cash_after": cash_after,
            "position_after_qty": position_update["qty"],
            "reference_price": execution_adjustment["reference_price"],
            "slippage_bps": execution_adjustment["slippage_bps"],
            "price_impact": execution_adjustment["price_impact"],
        }

        self.db.save_paper_order(
            {
                "client_order_id": client_order_id,
                "broker_order_id": broker_order_id,
                "event_id": event_id,
                "ticker": normalized_ticker,
                "side": normalized_side,
                "qty": qty,
                "order_type": order_type,
                "limit_price": None,
                "status": "filled",
                "filled_qty": qty,
                "filled_avg_price": fill_price,
                "submitted_at": now_iso,
                "notes": notes,
                "detail_json": merged_detail,
            }
        )
        self.db.save_paper_fill(
            {
                "client_order_id": client_order_id,
                "broker_order_id": broker_order_id,
                "event_id": event_id,
                "ticker": normalized_ticker,
                "side": normalized_side,
                "qty": qty,
                "fill_price": fill_price,
                "filled_at": now_iso,
                "commission": commission,
                "detail_json": merged_detail,
            }
        )
        self.db.save_order_execution(
            {
                "event_id": event_id,
                "ticker": normalized_ticker,
                "side": normalized_side,
                "qty": qty,
                "order_type": order_type,
                "submitted_at": now_iso,
                "filled_at": now_iso,
                "fill_price": fill_price,
                "result": "PAPER_FILLED",
                "broker_order_id": broker_order_id,
                "detail_json": merged_detail,
            }
        )

        if abs(position_update["qty"]) < 1e-9:
            self.db.delete_paper_position(normalized_ticker)
        else:
            self.db.upsert_paper_position(
                {
                    "ticker": normalized_ticker,
                    "qty": position_update["qty"],
                    "avg_price": position_update["avg_price"],
                    "market_price": fill_price,
                    "market_value": position_update["qty"] * fill_price,
                    "realized_pnl": position_update["realized_pnl"],
                    "unrealized_pnl": 0.0,
                }
            )

        account_realized = float(account.get("realized_pnl", 0.0)) + position_update["realized_delta"]
        self.db.update_paper_account_state(
            {
                "cash_balance": cash_after,
                "realized_pnl": account_realized,
            }
        )
        account_after = self.refresh_market_prices({normalized_ticker: fill_price})
        return {
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
            "event_id": event_id,
            "ticker": normalized_ticker,
            "side": normalized_side,
            "qty": qty,
            "fill_price": fill_price,
            "commission": commission,
            "account": account_after,
            "position": self.db.get_paper_position(normalized_ticker),
        }

    def _apply_fill_to_position(self, position: dict[str, Any], *, signed_delta: float, fill_price: float) -> dict[str, float]:
        current_qty = float(position.get("qty", 0.0) or 0.0)
        current_avg = float(position.get("avg_price", 0.0) or 0.0)
        current_realized = float(position.get("realized_pnl", 0.0) or 0.0)

        if abs(current_qty) < 1e-9:
            return {
                "qty": signed_delta,
                "avg_price": fill_price,
                "realized_pnl": current_realized,
                "realized_delta": 0.0,
            }

        same_direction = (current_qty > 0 and signed_delta > 0) or (current_qty < 0 and signed_delta < 0)
        if same_direction:
            new_qty = current_qty + signed_delta
            weighted_cost = (abs(current_qty) * current_avg) + (abs(signed_delta) * fill_price)
            new_avg = weighted_cost / abs(new_qty)
            return {
                "qty": new_qty,
                "avg_price": new_avg,
                "realized_pnl": current_realized,
                "realized_delta": 0.0,
            }

        closing_qty = min(abs(current_qty), abs(signed_delta))
        if current_qty > 0:
            realized_delta = (fill_price - current_avg) * closing_qty
        else:
            realized_delta = (current_avg - fill_price) * closing_qty
        new_qty = current_qty + signed_delta
        new_realized = current_realized + realized_delta

        if abs(new_qty) < 1e-9:
            return {
                "qty": 0.0,
                "avg_price": 0.0,
                "realized_pnl": new_realized,
                "realized_delta": realized_delta,
            }

        if (current_qty > 0 and new_qty > 0) or (current_qty < 0 and new_qty < 0):
            return {
                "qty": new_qty,
                "avg_price": current_avg,
                "realized_pnl": new_realized,
                "realized_delta": realized_delta,
            }

        return {
            "qty": new_qty,
            "avg_price": fill_price,
            "realized_pnl": new_realized,
            "realized_delta": realized_delta,
        }
