import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from market_data_provider import MarketDataProvider


class FakeResolvingMarketDataProvider(MarketDataProvider):
    def __init__(self, available: set[str]):
        super().__init__()
        self.available = available

    def _candidate_has_data(self, provider_ticker: str) -> bool:
        return provider_ticker in self.available


class MarketDataProviderResolutionTests(unittest.TestCase):
    def test_korean_plain_numeric_ticker_resolves_to_kospi_when_available(self):
        provider = FakeResolvingMarketDataProvider({"005930.KS"})

        instrument = provider.resolve_instrument("005930")

        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.provider_ticker, "005930.KS")
        self.assertEqual(instrument.market, "korea_kospi")
        self.assertEqual(instrument.currency_hint, "KRW")
        self.assertEqual(provider.benchmark_for_ticker("005930"), "069500.KS")

    def test_market_hint_controls_ambiguous_numeric_tickers(self):
        provider = FakeResolvingMarketDataProvider({"035720.KQ", "600519.SS"})

        kosdaq = provider.resolve_instrument("KOSDAQ:035720")
        china = provider.resolve_instrument("CN:600519")

        self.assertEqual(kosdaq.provider_ticker, "035720.KQ")
        self.assertEqual(kosdaq.exchange_hint, "KOSDAQ")
        self.assertEqual(china.provider_ticker, "600519.SS")
        self.assertEqual(china.exchange_hint, "SSE")

    def test_global_market_hints_apply_yahoo_suffixes(self):
        provider = FakeResolvingMarketDataProvider({"0700.HK", "7203.T", "SHOP.TO", "VOD.L"})

        self.assertEqual(provider.resolve_instrument("HK:700").provider_ticker, "0700.HK")
        self.assertEqual(provider.resolve_instrument("JP:7203").provider_ticker, "7203.T")
        self.assertEqual(provider.resolve_instrument("TSX:SHOP").provider_ticker, "SHOP.TO")
        self.assertEqual(provider.resolve_instrument("LSE:VOD").provider_ticker, "VOD.L")

    def test_us_and_existing_yahoo_symbols_pass_through(self):
        provider = FakeResolvingMarketDataProvider({"NVDA", "005930.KS"})

        self.assertEqual(provider.resolve_instrument("NVDA").provider_ticker, "NVDA")
        self.assertEqual(provider.resolve_instrument("005930.KS").provider_ticker, "005930.KS")


if __name__ == "__main__":
    unittest.main()
