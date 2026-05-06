from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


class DARTOfficialFetcher:
    """
    OpenDART 공식 API 기반 한국 상장사 데이터 fetcher.

    DART_API_KEY가 설정된 경우 회사개황, 최근 공시, 단일회사 주요계정을 수집한다.
    """

    CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
    COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
    DISCLOSURE_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
    SINGLE_ACCOUNT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

    REPORT_CODES = [
        ("11011", "사업보고서"),
        ("11014", "3분기보고서"),
        ("11012", "반기보고서"),
        ("11013", "1분기보고서"),
    ]

    def __init__(self, cache_dir: str | None = None, timeout_sec: float | None = None):
        root = Path(__file__).resolve().parents[2]
        self.cache_dir = Path(cache_dir or root / "data" / "official_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_sec = float(timeout_sec or os.getenv("DART_REQUEST_TIMEOUT_SEC", "12"))
        self.api_key = os.getenv("DART_API_KEY", "").strip()

    def is_supported_ticker(self, ticker: str) -> bool:
        return self._stock_code(ticker) is not None

    def _stock_code(self, ticker: str) -> str | None:
        text = str(ticker or "").strip().upper()
        m = re.fullmatch(r"(\d{6})(?:\.(KS|KQ|KONEX))?", text)
        if not m:
            return None
        return m.group(1)

    def render_official_fact_sheet(self, ticker: str) -> str:
        stock_code = self._stock_code(ticker)
        label = str(ticker or "").strip().upper()
        if not stock_code:
            return (
                f"**[공식 기업 데이터: {label} | DART]**\n"
                "- DART 대상 한국 6자리 종목코드가 아닙니다.\n"
            )
        if not self.api_key:
            return (
                f"**[공식 기업 데이터: {label} | DART]**\n"
                "- DART_API_KEY가 없어 OpenDART 공식 공시/재무 데이터를 수집하지 못했습니다.\n"
                "- 실제 투자 전 DART, KRX/KIND, 기업 IR에서 공시와 재무를 직접 확인해야 합니다.\n"
            )

        profile = self.get_company_profile(stock_code)
        if profile.get("status") != "ok":
            return (
                f"**[공식 기업 데이터: {label} | DART]**\n"
                f"- OpenDART 수집 실패: {profile.get('message') or profile.get('status')}\n"
                "- 실제 투자 전 DART/KRX/KIND/기업 IR 확인이 필요합니다.\n"
            )

        lines = [
            f"**[공식 기업 데이터: {label} | OpenDART]**",
            f"- 회사명: {profile.get('corp_name') or '-'} | 고유번호: {profile.get('corp_code')} | 종목코드: {stock_code}",
            f"- 법인구분: {profile.get('corp_cls') or '-'} | 업종코드: {profile.get('induty_code') or '-'}",
            f"- 대표자: {profile.get('ceo_nm') or '-'} | 결산월: {profile.get('acc_mt') or '-'}",
            f"- 출처: {self.COMPANY_URL} | {self.DISCLOSURE_LIST_URL} | {self.SINGLE_ACCOUNT_URL}",
        ]

        filings = profile.get("recent_filings") or []
        if filings:
            lines.append("- 최근 공시:")
            for filing in filings[:8]:
                url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filing.get('rcept_no')}"
                lines.append(
                    f"  - {filing.get('rcept_dt')} {filing.get('report_nm')} "
                    f"접수번호={filing.get('rcept_no')} url={url}"
                )

        accounts = profile.get("major_accounts") or []
        if accounts:
            report_name = profile.get("major_account_report_name") or ""
            year = profile.get("major_account_year") or ""
            lines.append(f"- 주요계정({year} {report_name}):")
            for row in accounts[:12]:
                lines.append(
                    f"  - {row.get('account_nm')}: 당기={row.get('thstrm_amount') or '-'} "
                    f"전기={row.get('frmtrm_amount') or '-'} fs={row.get('fs_nm') or row.get('fs_div') or '-'}"
                )
        else:
            lines.append("- 주요계정: 최근 연차/분기 주요계정 데이터를 찾지 못했습니다.")

        lines.append("- 주의: OpenDART 데이터는 공시 기반입니다. 실시간 주가/호가/체결 가능 가격이 아닙니다.")
        return "\n".join(lines) + "\n"

    def get_company_profile(self, stock_code: str) -> dict[str, Any]:
        corp = self.lookup_corp(stock_code)
        if not corp:
            return {"status": "not_found", "message": "corp_code mapping not found"}
        corp_code = corp["corp_code"]
        company = self._get_json(self.COMPANY_URL, {"corp_code": corp_code}) or {}
        disclosures = self._recent_disclosures(corp_code)
        accounts, year, report_code, report_name = self._latest_major_accounts(corp_code)
        if company.get("status") not in {None, "000"}:
            return {"status": "error", "message": company.get("message", ""), "corp_code": corp_code}
        return {
            "status": "ok",
            "corp_code": corp_code,
            "stock_code": stock_code,
            "corp_name": company.get("corp_name") or corp.get("corp_name"),
            "corp_cls": company.get("corp_cls"),
            "ceo_nm": company.get("ceo_nm"),
            "induty_code": company.get("induty_code"),
            "acc_mt": company.get("acc_mt"),
            "recent_filings": disclosures,
            "major_accounts": accounts,
            "major_account_year": year,
            "major_account_report_code": report_code,
            "major_account_report_name": report_name,
        }

    def lookup_corp(self, stock_code: str) -> dict[str, str] | None:
        for row in self._corp_codes():
            if row.get("stock_code") == stock_code:
                return row
        return None

    def _corp_codes(self) -> list[dict[str, str]]:
        cache_path = self.cache_dir / "dart_corp_codes.json"
        if cache_path.exists():
            age_hours = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 3600.0
            if age_hours <= 24:
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        if not self.api_key:
            return []
        zip_path = self.cache_dir / "dart_corp_code.zip"
        url = f"{self.CORP_CODE_URL}?{urllib.parse.urlencode({'crtfc_key': self.api_key})}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_sec) as resp:
                if getattr(resp, "status", 200) != 200:
                    return []
                zip_path.write_bytes(resp.read())
            with zipfile.ZipFile(zip_path) as zf:
                xml_name = next((name for name in zf.namelist() if name.upper().endswith(".XML")), "")
                if not xml_name:
                    return []
                root = ElementTree.fromstring(zf.read(xml_name))
            rows = []
            for elem in root.findall("list"):
                row = {child.tag: (child.text or "").strip() for child in elem}
                if row.get("stock_code"):
                    rows.append(row)
            cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            return rows
        except Exception:
            return []

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self.api_key:
            return None
        query = {"crtfc_key": self.api_key, **params}
        req_url = f"{url}?{urllib.parse.urlencode(query)}"
        try:
            req = urllib.request.Request(req_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                if getattr(resp, "status", 200) != 200:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _recent_disclosures(self, corp_code: str) -> list[dict[str, Any]]:
        end = datetime.now()
        start = end - timedelta(days=365)
        payload = self._get_json(
            self.DISCLOSURE_LIST_URL,
            {
                "corp_code": corp_code,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "last_reprt_at": "Y",
                "sort": "date",
                "sort_mth": "desc",
                "page_no": "1",
                "page_count": "20",
            },
        ) or {}
        if payload.get("status") != "000":
            return []
        rows = payload.get("list") or []
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(
                    {
                        "rcept_no": row.get("rcept_no", ""),
                        "rcept_dt": row.get("rcept_dt", ""),
                        "report_nm": row.get("report_nm", ""),
                        "corp_name": row.get("corp_name", ""),
                    }
                )
        return out

    def _latest_major_accounts(self, corp_code: str) -> tuple[list[dict[str, Any]], str, str, str]:
        current_year = datetime.now().year
        candidates: list[tuple[int, str, str]] = []
        for year in [current_year, current_year - 1, current_year - 2]:
            for code, name in self.REPORT_CODES:
                candidates.append((year, code, name))

        for year, report_code, report_name in candidates:
            payload = self._get_json(
                self.SINGLE_ACCOUNT_URL,
                {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": report_code,
                },
            ) or {}
            if payload.get("status") != "000":
                continue
            rows = payload.get("list") or []
            if not rows:
                continue
            cleaned = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cleaned.append(
                    {
                        "account_nm": row.get("account_nm", ""),
                        "fs_div": row.get("fs_div", ""),
                        "fs_nm": row.get("fs_nm", ""),
                        "sj_div": row.get("sj_div", ""),
                        "thstrm_amount": row.get("thstrm_amount", ""),
                        "frmtrm_amount": row.get("frmtrm_amount", ""),
                        "bfefrmtrm_amount": row.get("bfefrmtrm_amount", ""),
                    }
                )
            return cleaned, str(year), report_code, report_name
        return [], "", "", ""
