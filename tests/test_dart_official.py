import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from data_fetcher.dart_official import DARTOfficialFetcher


class FakeDARTOfficialFetcher(DARTOfficialFetcher):
    def __init__(self, api_key: str = "test-key"):
        self.cache_dir_ctx = tempfile.TemporaryDirectory()
        old_key = os.environ.get("DART_API_KEY")
        os.environ["DART_API_KEY"] = api_key
        try:
            super().__init__(cache_dir=self.cache_dir_ctx.name)
        finally:
            if old_key is None:
                os.environ.pop("DART_API_KEY", None)
            else:
                os.environ["DART_API_KEY"] = old_key
        self.api_key = api_key

    def cleanup(self):
        self.cache_dir_ctx.cleanup()

    def _corp_codes(self):
        return [
            {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "stock_code": "005930",
            }
        ]

    def _get_json(self, url: str, params: dict):
        if "company.json" in url:
            return {
                "status": "000",
                "corp_name": "삼성전자",
                "corp_cls": "Y",
                "ceo_nm": "한종희",
                "induty_code": "264",
                "acc_mt": "12",
            }
        if "list.json" in url:
            return {
                "status": "000",
                "list": [
                    {
                        "rcept_no": "20260401000123",
                        "rcept_dt": "20260401",
                        "report_nm": "사업보고서",
                        "corp_name": "삼성전자",
                    }
                ],
            }
        if "fnlttSinglAcnt.json" in url:
            return {
                "status": "000",
                "list": [
                    {
                        "account_nm": "매출액",
                        "fs_div": "CFS",
                        "fs_nm": "연결재무제표",
                        "sj_div": "IS",
                        "thstrm_amount": "300000000",
                        "frmtrm_amount": "250000000",
                    },
                    {
                        "account_nm": "당기순이익",
                        "fs_div": "CFS",
                        "fs_nm": "연결재무제표",
                        "sj_div": "IS",
                        "thstrm_amount": "40000000",
                        "frmtrm_amount": "30000000",
                    },
                ],
            }
        return None


class DARTOfficialFetcherTests(unittest.TestCase):
    def test_render_official_fact_sheet_uses_dart_company_filings_and_accounts(self):
        fetcher = FakeDARTOfficialFetcher()
        try:
            text = fetcher.render_official_fact_sheet("005930.KS")
        finally:
            fetcher.cleanup()

        self.assertIn("OpenDART", text)
        self.assertIn("삼성전자", text)
        self.assertIn("20260401 사업보고서", text)
        self.assertIn("매출액: 당기=300000000", text)
        self.assertIn("당기순이익: 당기=40000000", text)
        self.assertIn("실시간 주가/호가/체결 가능 가격이 아닙니다", text)

    def test_missing_api_key_returns_configuration_warning(self):
        fetcher = FakeDARTOfficialFetcher(api_key="")
        try:
            text = fetcher.render_official_fact_sheet("005930")
        finally:
            fetcher.cleanup()

        self.assertIn("DART_API_KEY가 없어", text)
        self.assertIn("OpenDART 공식 공시/재무", text)

    def test_non_korean_ticker_is_not_supported(self):
        fetcher = FakeDARTOfficialFetcher()
        try:
            text = fetcher.render_official_fact_sheet("NVDA")
        finally:
            fetcher.cleanup()

        self.assertIn("DART 대상 한국 6자리 종목코드가 아닙니다", text)


if __name__ == "__main__":
    unittest.main()
