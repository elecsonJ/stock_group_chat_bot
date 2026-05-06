import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from risk_manager import RiskManager


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.db.reset_paper_account_state(1000.0)
        self.risk = RiskManager(self.db)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def test_explicit_size_rule_is_capped_by_ticker_exposure(self):
        ok, reason, plan = self.risk.evaluate_event(
            event_id="SG-RISK-1",
            recommendations=[
                {
                    "ticker": "NVDA",
                    "side": "BUY",
                    "size_rule": "3 units",
                    "confidence": 0.9,
                    "rationale": "test",
                }
            ],
            prices={"NVDA": 100.0},
        )

        self.assertTrue(ok, reason)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["qty"], 2.0)
        self.assertEqual(plan[0]["notional"], 200.0)

    def test_sell_is_blocked_when_shorts_disabled_without_long_position(self):
        ok, reason, plan = self.risk.evaluate_event(
            event_id="SG-RISK-2",
            recommendations=[
                {
                    "ticker": "TSLA",
                    "side": "SELL",
                    "size_rule": "1 unit",
                    "confidence": 0.5,
                    "rationale": "test",
                }
            ],
            prices={"TSLA": 100.0},
        )

        self.assertFalse(ok)
        self.assertIn("숏 비활성화", reason)
        self.assertEqual(plan, [])

    def test_sell_existing_long_is_allowed_and_reduces_exposure(self):
        self.db.upsert_paper_position(
            {
                "ticker": "NVDA",
                "qty": 2.0,
                "avg_price": 100.0,
                "market_price": 100.0,
                "market_value": 200.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            }
        )

        ok, reason, plan = self.risk.evaluate_event(
            event_id="SG-RISK-3",
            recommendations=[
                {
                    "ticker": "NVDA",
                    "side": "SELL",
                    "size_rule": "1 unit",
                    "confidence": 0.5,
                    "rationale": "reduce position",
                }
            ],
            prices={"NVDA": 100.0},
        )

        self.assertTrue(ok, reason)
        self.assertEqual(plan[0]["qty"], 1.0)
        self.assertEqual(plan[0]["side"], "SELL")


if __name__ == "__main__":
    unittest.main()
