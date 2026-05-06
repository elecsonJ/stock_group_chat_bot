import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from data_fetcher.sec_official import SECOfficialFetcher


class FakeSECOfficialFetcher(SECOfficialFetcher):
    def __init__(self):
        self.cache_dir_ctx = tempfile.TemporaryDirectory()
        super().__init__(cache_dir=self.cache_dir_ctx.name)

    def cleanup(self):
        self.cache_dir_ctx.cleanup()

    def _get_json(self, url: str, cache_name: str | None = None, cache_ttl_hours: int = 12):
        if "company_tickers" in url:
            return {
                "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            }
        if "submissions" in url:
            return {
                "name": "NVIDIA CORP",
                "sic": "3674",
                "sicDescription": "Semiconductors and Related Devices",
                "exchanges": ["Nasdaq"],
                "filings": {
                    "recent": {
                        "form": ["10-K", "8-K", "4"],
                        "filingDate": ["2026-03-01", "2026-02-20", "2026-02-18"],
                        "reportDate": ["2026-01-31", "2026-02-20", ""],
                        "accessionNumber": ["0001045810-26-000010", "0001045810-26-000008", "x"],
                        "primaryDocument": ["nvda-20260131.htm", "nvda-8k.htm", "x.htm"],
                    }
                },
            }
        if "companyfacts" in url:
            return {
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {"val": 100, "end": "2025-01-31", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-03-01"},
                                    {"val": 130, "end": "2026-01-31", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-03-01"},
                                ]
                            }
                        },
                        "NetIncomeLoss": {
                            "units": {
                                "USD": [
                                    {"val": 50, "end": "2026-01-31", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-03-01"}
                                ]
                            }
                        },
                    }
                }
            }
        return None


class SECOfficialFetcherTests(unittest.TestCase):
    def test_render_official_fact_sheet_uses_sec_facts_and_filings(self):
        fetcher = FakeSECOfficialFetcher()
        try:
            text = fetcher.render_official_fact_sheet("NVDA")
        finally:
            fetcher.cleanup()

        self.assertIn("SEC EDGAR/XBRL", text)
        self.assertIn("NVIDIA CORP", text)
        self.assertIn("10-K filed=2026-03-01", text)
        self.assertIn("Revenue: $130", text)
        self.assertIn("Net income: $50", text)
        self.assertIn("실시간 주가/호가/체결 가능 가격이 아닙니다", text)

    def test_non_us_ticker_returns_source_limitation(self):
        fetcher = FakeSECOfficialFetcher()
        try:
            text = fetcher.render_official_fact_sheet("005930.KS")
        finally:
            fetcher.cleanup()

        self.assertIn("SEC EDGAR 매핑 없음", text)
        self.assertIn("DART/KRX/KIND", text)


if __name__ == "__main__":
    unittest.main()
