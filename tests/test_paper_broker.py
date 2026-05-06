import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from paper_broker import PaperBroker


class PaperBrokerTests(unittest.TestCase):
    def setUp(self):
        self.prev_slippage = os.environ.get("PAPER_SLIPPAGE_BPS")
        self.prev_spread = os.environ.get("PAPER_SPREAD_BPS")
        self.prev_urgency = os.environ.get("PAPER_IMMEDIATE_URGENCY_BPS")
        os.environ["PAPER_SLIPPAGE_BPS"] = "0"
        os.environ["PAPER_SPREAD_BPS"] = "0"
        os.environ["PAPER_IMMEDIATE_URGENCY_BPS"] = "0"
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.db.reset_paper_account_state(10000.0)
        self.broker = PaperBroker(self.db)

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

    def test_submit_market_order_updates_account_and_position(self):
        result = self.broker.submit_market_order(
            event_id="SG-BROKER-1",
            ticker="NVDA",
            side="BUY",
            qty=2,
            fill_price=100.0,
            detail_json={"reason": "unit test"},
        )

        self.assertEqual(result["ticker"], "NVDA")
        self.assertEqual(len(self.db.list_paper_orders()), 1)
        self.assertEqual(len(self.db.list_paper_fills()), 1)
        position = self.db.get_paper_position("NVDA")
        self.assertIsNotNone(position)
        self.assertEqual(position["qty"], 2.0)
        self.assertEqual(position["avg_price"], 100.0)
        account = self.db.get_paper_account_state()
        self.assertAlmostEqual(account["cash_balance"], 9800.0, places=4)
        self.assertAlmostEqual(account["equity"], 10000.0, places=4)

    def test_sell_reduces_position_and_realizes_pnl(self):
        self.broker.submit_market_order(
            event_id="SG-BROKER-2",
            ticker="NVDA",
            side="BUY",
            qty=2,
            fill_price=100.0,
        )

        self.broker.submit_market_order(
            event_id="SG-BROKER-2",
            ticker="NVDA",
            side="SELL",
            qty=1,
            fill_price=120.0,
        )

        position = self.db.get_paper_position("NVDA")
        self.assertIsNotNone(position)
        self.assertEqual(position["qty"], 1.0)
        self.assertEqual(position["avg_price"], 100.0)
        self.assertEqual(position["realized_pnl"], 20.0)
        account = self.db.get_paper_account_state()
        self.assertEqual(account["realized_pnl"], 20.0)


if __name__ == "__main__":
    unittest.main()
