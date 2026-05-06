import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from trading_executor import TradingExecutor


class StubTradingExecutor(TradingExecutor):
    def __init__(self, db: DBManager, prices: dict[str, float]):
        super().__init__(db)
        self._prices = prices

    def _fetch_price(self, ticker: str) -> float:
        return float(self._prices.get(ticker, 0.0))

    def _fetch_market_context(self, ticker: str) -> dict[str, float]:
        return {}


class TradingExecutorTests(unittest.TestCase):
    def setUp(self):
        self.prev_slippage = os.environ.get("PAPER_SLIPPAGE_BPS")
        self.prev_spread = os.environ.get("PAPER_SPREAD_BPS")
        self.prev_urgency = os.environ.get("PAPER_IMMEDIATE_URGENCY_BPS")
        os.environ["PAPER_SLIPPAGE_BPS"] = "0"
        os.environ["PAPER_SPREAD_BPS"] = "0"
        os.environ["PAPER_IMMEDIATE_URGENCY_BPS"] = "0"
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.db.set_kill_switch(False)
        self.event_id = "SG-TRADE-1"
        self.db.upsert_signal_event(
            {
                "event_id": self.event_id,
                "event_key": "EVT-TRADE-1",
                "date": "2026-04-05",
                "detected_at": "2026-04-05T09:00:00",
                "title": "NVIDIA contract award",
                "summary": "NVIDIA contract award raised guidance",
                "score_total": 90.0,
                "score_json": {},
                "related_tickers": ["NVDA"],
                "direction": "bullish",
                "urgency": "same_day",
                "confidence": 0.95,
                "status": "pending_approval",
                "evidence_ids": [],
                "verification_json": {},
                "last_verified_at": None,
            }
        )
        self.db.replace_recommendations(
            self.event_id,
            [
                {
                    "ticker": "NVDA",
                    "side": "BUY",
                    "size_rule": "1 unit",
                    "entry_rule": "market",
                    "stop_rule": "-1%",
                    "ttl_sec": 900,
                    "confidence": 0.9,
                    "rationale": "test rationale",
                    "status": "pending_approval",
                }
            ],
        )
        self.db.upsert_approval_request(self.event_id, ttl_sec=900)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()
        if self.prev_slippage is None:
            os.environ.pop("PAPER_SLIPPAGE_BPS", None)
        else:
            os.environ["PAPER_SLIPPAGE_BPS"] = self.prev_slippage
        if self.prev_spread is None:
            os.environ.pop("PAPER_SPREAD_BPS", None)
        else:
            os.environ["PAPER_SPREAD_BPS"] = self.prev_spread
        if self.prev_urgency is None:
            os.environ.pop("PAPER_IMMEDIATE_URGENCY_BPS", None)
        else:
            os.environ["PAPER_IMMEDIATE_URGENCY_BPS"] = self.prev_urgency

    def test_execute_paper_fails_closed_when_price_missing(self):
        executor = StubTradingExecutor(self.db, prices={"NVDA": 0.0})

        ok, message = executor.execute_paper(self.event_id, approved_by="tester")

        self.assertFalse(ok)
        self.assertIn("가격 조회 실패", message)
        self.assertEqual(self.db.count_orders_in_window("1970-01-01T00:00:00"), 0)
        self.assertEqual(self.db.get_approval_request(self.event_id)["state"], "pending")

    def test_execute_paper_writes_order_when_price_exists(self):
        executor = StubTradingExecutor(self.db, prices={"NVDA": 123.45})

        ok, message = executor.execute_paper(self.event_id, approved_by="tester")

        self.assertTrue(ok)
        self.assertIn("페이퍼 체결 완료", message)
        self.assertEqual(self.db.count_orders_in_window("1970-01-01T00:00:00"), 1)
        self.assertEqual(self.db.get_approval_request(self.event_id)["state"], "executed")
        self.assertEqual(self.db.get_signal_event(self.event_id)["status"], "executed")
        self.assertEqual(len(self.db.list_paper_orders()), 1)
        self.assertEqual(len(self.db.list_paper_fills()), 1)
        position = self.db.get_paper_position("NVDA")
        self.assertIsNotNone(position)
        self.assertEqual(position["qty"], 1.0)
        self.assertEqual(position["avg_price"], 123.45)
        account = self.db.get_paper_account_state()
        self.assertAlmostEqual(account["cash_balance"], 100000.0 - 123.45, places=4)


if __name__ == "__main__":
    unittest.main()
