import os
import sys
import unittest
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from market_data_adapter import HistoricalPriceRequest, QuoteRequest, YFinanceReferenceAdapter
from market_data_provider import PriceQuote


class FakeProvider:
    provider = "fake"

    def get_latest_quote(self, ticker):
        return PriceQuote(ticker=ticker, price=10.0)

    def get_historical_price(self, ticker, when):
        return PriceQuote(ticker=ticker, price=9.5, as_of=when.strftime("%Y-%m-%dT%H:%M:%S"))

    def assess_quote_quality(self, quote, max_age_minutes=30):
        return {"state": "reference" if quote else "missing", "tradable": bool(quote)}


class MarketDataAdapterTests(unittest.TestCase):
    def test_yfinance_reference_adapter_rejects_execution_grade_request(self):
        adapter = YFinanceReferenceAdapter(FakeProvider())

        quote = adapter.get_latest_quote(QuoteRequest("NVDA", require_execution_grade=True))

        self.assertIsNone(quote)
        self.assertFalse(adapter.capabilities().execution_grade)

    def test_yfinance_reference_adapter_applies_market_hint(self):
        adapter = YFinanceReferenceAdapter(FakeProvider())

        quote = adapter.get_latest_quote(QuoteRequest("005930", market_hint="KR"))
        hist = adapter.get_historical_price(HistoricalPriceRequest("7203", datetime(2026, 5, 24), market_hint="JP"))

        self.assertEqual(quote.ticker, "KR:005930")
        self.assertEqual(hist.ticker, "JP:7203")


if __name__ == "__main__":
    unittest.main()
