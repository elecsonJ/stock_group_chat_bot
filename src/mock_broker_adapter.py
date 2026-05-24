from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from broker_adapter import BrokerAdapter


class MockBrokerAdapter(BrokerAdapter):
    """In-memory broker lifecycle simulator for adapter contract tests."""

    def __init__(self, starting_cash: float = 100000.0):
        self.account = {
            "broker_name": "mock",
            "mode": "sandbox_mock",
            "cash_balance": float(starting_cash),
            "equity": float(starting_cash),
            "buying_power": float(starting_cash),
        }
        self.positions: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.fills: list[dict[str, Any]] = []

    def get_account_state(self) -> dict[str, Any]:
        return dict(self.account)

    def get_positions(self) -> list[dict[str, Any]]:
        return [dict(pos) for pos in self.positions.values()]

    def get_open_orders(self) -> list[dict[str, Any]]:
        return [dict(order) for order in self.orders.values() if order.get("status") in {"submitted", "pending", "partially_filled"}]

    def get_fills(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(fill) for fill in self.fills[-int(limit):]]

    def refresh_market_prices(self, price_map: dict[str, float]) -> dict[str, Any]:
        market_value = 0.0
        for ticker, pos in self.positions.items():
            px = float(price_map.get(ticker, pos.get("market_price", pos.get("avg_price", 0.0))) or 0.0)
            pos["market_price"] = px
            pos["market_value"] = float(pos.get("qty", 0.0) or 0.0) * px
            market_value += pos["market_value"]
        self.account["equity"] = self.account["cash_balance"] + market_value
        self.account["buying_power"] = self.account["cash_balance"]
        return self.get_account_state()

    def submit_market_order(
        self,
        *,
        event_id: str,
        ticker: str,
        side: str,
        qty: float,
        fill_price: float,
        order_type: str = "mock_market",
        notes: str = "",
        detail_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ticker = str(ticker).upper().strip()
        side = str(side).upper().strip()
        qty = float(qty)
        fill_price = float(fill_price)
        if not ticker or side not in {"BUY", "SELL"} or qty <= 0 or fill_price <= 0:
            raise ValueError("invalid mock order")
        client_order_id = f"MOCK-{uuid4().hex[:12].upper()}"
        broker_order_id = f"MOCKBR-{uuid4().hex[:12].upper()}"
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        signed_qty = qty if side == "BUY" else -qty
        cash_delta = -qty * fill_price if side == "BUY" else qty * fill_price
        self.account["cash_balance"] += cash_delta
        existing = self.positions.get(ticker, {"ticker": ticker, "qty": 0.0, "avg_price": 0.0, "market_price": fill_price})
        new_qty = float(existing["qty"]) + signed_qty
        avg_price = fill_price if abs(new_qty) > 0 else 0.0
        self.positions[ticker] = {
            "ticker": ticker,
            "qty": new_qty,
            "avg_price": avg_price,
            "market_price": fill_price,
            "market_value": new_qty * fill_price,
        }
        if abs(new_qty) < 1e-9:
            self.positions.pop(ticker, None)
        order = {
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
            "event_id": event_id,
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "status": "filled",
            "filled_qty": qty,
            "filled_avg_price": fill_price,
            "submitted_at": now,
            "updated_at": now,
            "notes": notes,
            "detail_json": detail_json or {},
        }
        self.orders[client_order_id] = order
        self.fills.append({**order, "fill_price": fill_price, "filled_at": now})
        self.refresh_market_prices({ticker: fill_price})
        return {**order, "account": self.get_account_state(), "position": self.positions.get(ticker)}

    def cancel_order(self, client_order_id: str) -> dict[str, Any]:
        order = self.orders.get(client_order_id)
        if not order:
            return {"ok": False, "status": "not_found", "client_order_id": client_order_id}
        if order.get("status") == "filled":
            return {"ok": False, "status": "already_filled", "client_order_id": client_order_id}
        order["status"] = "canceled"
        order["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        return {"ok": True, "status": "canceled", "client_order_id": client_order_id}
