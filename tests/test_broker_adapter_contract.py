import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from mock_broker_adapter import MockBrokerAdapter


class BrokerAdapterContractTests(unittest.TestCase):
    def test_mock_broker_market_order_lifecycle(self):
        broker = MockBrokerAdapter(starting_cash=10000.0)

        result = broker.submit_market_order(
            event_id="SG-MOCK-1",
            ticker="NVDA",
            side="BUY",
            qty=2,
            fill_price=100.0,
        )

        self.assertEqual(result["status"], "filled")
        self.assertEqual(len(broker.get_fills()), 1)
        self.assertEqual(broker.get_positions()[0]["ticker"], "NVDA")
        self.assertEqual(broker.get_open_orders(), [])
        self.assertEqual(broker.get_account_state()["cash_balance"], 9800.0)

    def test_mock_broker_rejects_cancel_of_filled_order(self):
        broker = MockBrokerAdapter(starting_cash=10000.0)
        result = broker.submit_market_order(
            event_id="SG-MOCK-2",
            ticker="NVDA",
            side="BUY",
            qty=1,
            fill_price=100.0,
        )

        cancel = broker.cancel_order(result["client_order_id"])

        self.assertFalse(cancel["ok"])
        self.assertEqual(cancel["status"], "already_filled")


if __name__ == "__main__":
    unittest.main()
