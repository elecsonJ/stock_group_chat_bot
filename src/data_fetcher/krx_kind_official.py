from __future__ import annotations

import re


class KRXKindOfficialChecker:
    """
    한국 시장 무결성 체크 placeholder.

    OpenDART가 공시/재무를 담당하고, KRX/KIND는 가격/시장조치/거래정지/시장경보
    provider로 확장할 자리다. 현재는 실제 투자 전 확인 항목을 Fact-Sheet에 강제 노출한다.
    """

    KRX_DATA_URL = "https://data.krx.co.kr"
    KIND_URL = "https://kind.krx.co.kr"

    def is_supported_ticker(self, ticker: str) -> bool:
        return self._stock_code(ticker) is not None

    def _stock_code(self, ticker: str) -> str | None:
        text = str(ticker or "").strip().upper()
        m = re.fullmatch(r"(\d{6})(?:\.(KS|KQ|KONEX))?", text)
        return m.group(1) if m else None

    def render_market_integrity_note(self, ticker: str) -> str:
        stock_code = self._stock_code(ticker)
        label = str(ticker or "").strip().upper()
        if not stock_code:
            return ""
        return (
            f"**[한국 시장 무결성 체크 필요: {label} | KRX/KIND]**\n"
            f"- 확인 대상: 종목코드 {stock_code}\n"
            f"- KRX Data: {self.KRX_DATA_URL} | KIND: {self.KIND_URL}\n"
            "- 실제 투자 전 거래정지/관리종목/투자주의·경고·위험/시장경보/공매도/수급/최근 정정공시를 확인해야 합니다.\n"
            "- 현재 자동 provider는 OpenDART 공시·재무 중심이며, KRX/KIND 세부 데이터는 다음 확장 대상입니다.\n"
        )
