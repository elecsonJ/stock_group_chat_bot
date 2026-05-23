from __future__ import annotations

from datetime import datetime
from typing import Any

from db_manager import DBManager


class PaperStateReconciler:
    """Check internal paper account consistency before broker adapters exist."""

    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()

    def run(self, *, persist: bool = True) -> dict[str, Any]:
        account = self.db.get_paper_account_state()
        positions = self.db.list_paper_positions()
        orders = self.db.list_paper_orders(limit=1000)
        fills = self.db.list_paper_fills(limit=1000)
        mismatches: list[dict[str, Any]] = []

        position_value = round(sum(float(p.get("market_value", 0.0) or 0.0) for p in positions), 4)
        cash = float(account.get("cash_balance", 0.0) or 0.0)
        equity_expected = round(cash + position_value, 4)
        equity_actual = round(float(account.get("equity", 0.0) or 0.0), 4)
        if abs(equity_expected - equity_actual) > 0.05:
            mismatches.append(
                {
                    "type": "equity_mismatch",
                    "expected": equity_expected,
                    "actual": equity_actual,
                    "cash": cash,
                    "position_value": position_value,
                }
            )

        fill_by_order: dict[str, float] = {}
        for fill in fills:
            cid = str(fill.get("client_order_id") or "")
            fill_by_order[cid] = fill_by_order.get(cid, 0.0) + float(fill.get("qty", 0.0) or 0.0)
        for order in orders:
            status = str(order.get("status") or "")
            cid = str(order.get("client_order_id") or "")
            filled_qty = float(order.get("filled_qty", 0.0) or 0.0)
            fill_qty = fill_by_order.get(cid, 0.0)
            if status in {"filled", "executed"} and abs(filled_qty - fill_qty) > 0.0001:
                mismatches.append(
                    {
                        "type": "order_fill_qty_mismatch",
                        "client_order_id": cid,
                        "order_filled_qty": filled_qty,
                        "fill_qty": fill_qty,
                    }
                )
            if status in {"submitted", "pending"} and filled_qty > 0:
                mismatches.append(
                    {
                        "type": "pending_order_has_filled_qty",
                        "client_order_id": cid,
                        "filled_qty": filled_qty,
                    }
                )

        status = "ok" if not mismatches else "mismatch"
        report = {
            "run_type": "paper",
            "status": status,
            "mismatch_count": len(mismatches),
            "checked_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "detail_json": {
                "account": account,
                "position_count": len(positions),
                "order_count": len(orders),
                "fill_count": len(fills),
                "mismatches": mismatches,
            },
        }
        if persist:
            self.db.save_reconciliation_run(report)
        return report

    def render(self, report: dict[str, Any]) -> str:
        lines = [
            "Paper Reconciliation",
            f"- status: {report.get('status')}",
            f"- mismatches: {report.get('mismatch_count', 0)}",
            f"- checked_at: {report.get('checked_at')}",
        ]
        mismatches = (report.get("detail_json") or {}).get("mismatches", [])
        for item in mismatches[:8]:
            lines.append(f"- {item.get('type')}: {item}")
        return "\n".join(lines)
