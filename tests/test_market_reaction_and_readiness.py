import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from live_readiness_check import LiveReadinessChecker
from market_data_provider import PriceQuote
from market_reaction import MarketReactionAnalyzer
from reconciliation import PaperStateReconciler


class FakeMarketData:
    def __init__(self):
        self.event_time = datetime(2026, 5, 24, 10, 0, 0)

    def get_historical_price(self, ticker, when):
        base = 100.0 if ticker == "NVDA" else 500.0
        minutes = int((when - self.event_time).total_seconds() / 60)
        if ticker == "NVDA":
            mapping = {-60: 95.0, -30: 98.0, -5: 100.0, 0: 100.0, 5: 101.0, 30: 102.0, 60: 103.0, 1440: 105.0}
        else:
            mapping = {0: base, 60: base * 1.002}
        price = mapping.get(minutes)
        if price is None:
            return None
        return PriceQuote(ticker=ticker, price=price, as_of=when.strftime("%Y-%m-%dT%H:%M:%S"), source="fake", quality="reference")

    def get_latest_quote(self, ticker):
        return PriceQuote(ticker=ticker, price=100.0, as_of=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), source="fake", quality="reference")

    def assess_quote_quality(self, quote, max_age_minutes=30):
        return {"state": "reference", "tradable": True, "price": quote.price}

    def benchmark_for_ticker(self, ticker):
        return "SPY"


class MarketReactionAndReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_market_reaction_snapshot_persists_relative_move(self):
        event_time = datetime(2026, 5, 24, 10, 0, 0)
        self.db.upsert_signal_event(
            {
                "event_id": "SG-MR-1",
                "event_key": "EVT-MR-1",
                "date": "2026-05-24",
                "detected_at": event_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "title": "NVDA event",
                "summary": "test",
                "score_total": 80.0,
                "score_json": {},
                "related_tickers": ["NVDA"],
                "direction": "bullish",
                "urgency": "same_day",
                "confidence": 0.8,
                "status": "monitor_only",
                "evidence_ids": [],
                "verification_json": {},
            }
        )
        analyzer = MarketReactionAnalyzer(self.db, FakeMarketData())

        rows = analyzer.capture_for_signal_event("SG-MR-1")

        self.assertEqual(len(rows), 1)
        saved = self.db.list_market_reaction_snapshots(event_id="SG-MR-1")
        self.assertEqual(len(saved), 1)
        self.assertAlmostEqual(saved[0]["post_60m_move_pct"], 3.0, places=2)
        self.assertGreater(saved[0]["relative_post_60m_pct"], 2.0)

    def test_reconciliation_detects_equity_mismatch_and_readiness_warns(self):
        self.db.update_paper_account_state({"cash_balance": 1000.0, "equity": 9999.0, "buying_power": 1000.0})
        reconciler = PaperStateReconciler(self.db)

        report = reconciler.run(persist=True)

        self.assertEqual(report["status"], "mismatch")
        self.assertGreater(report["mismatch_count"], 0)
        readiness = LiveReadinessChecker(self.db, FakeMarketData()).run(include_network=True)
        self.assertIn(readiness["overall_status"], {"fail", "warn"})


if __name__ == "__main__":
    unittest.main()
