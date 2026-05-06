from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class SECOfficialFetcher:
    """
    SEC EDGAR 공식 JSON API 기반 기업 데이터 fetcher.

    가격/호가 데이터가 아니라 기업이 제출한 filings와 XBRL facts를 수집한다.
    """

    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    METRIC_TAGS = {
        "Revenue": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
        "Net income": ["NetIncomeLoss", "ProfitLoss"],
        "Operating income": ["OperatingIncomeLoss"],
        "Assets": ["Assets"],
        "Liabilities": ["Liabilities"],
        "Stockholders equity": [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
        "Operating cash flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Capital expenditures": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ],
        "Diluted EPS": ["EarningsPerShareDiluted"],
        "Shares outstanding": [
            "EntityCommonStockSharesOutstanding",
            "CommonStocksIncludingAdditionalPaidInCapital",
        ],
    }

    def __init__(self, cache_dir: str | None = None, timeout_sec: float | None = None):
        root = Path(__file__).resolve().parents[2]
        self.cache_dir = Path(cache_dir or root / "data" / "official_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_sec = float(timeout_sec or os.getenv("SEC_REQUEST_TIMEOUT_SEC", "12"))
        self.user_agent = os.getenv("SEC_USER_AGENT", "").strip()

    def _headers(self) -> dict[str, str]:
        ua = self.user_agent or "stock_group_chat_bot/0.1 contact@example.com"
        return {
            "User-Agent": ua,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

    def _get_json(self, url: str, cache_name: str | None = None, cache_ttl_hours: int = 12) -> dict[str, Any] | None:
        cache_path = self.cache_dir / cache_name if cache_name else None
        if cache_path and cache_path.exists():
            age_hours = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 3600.0
            if age_hours <= cache_ttl_hours:
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        try:
            if requests is not None:
                resp = requests.get(url, headers=self._headers(), timeout=self.timeout_sec)
                if resp.status_code != 200:
                    return None
                payload = resp.json()
            else:
                headers = self._headers()
                headers.pop("Accept-Encoding", None)
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    if getattr(resp, "status", 200) != 200:
                        return None
                    payload = json.loads(resp.read().decode("utf-8"))
            if cache_path:
                cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except Exception:
            return None

    def _is_us_ticker(self, ticker: str) -> bool:
        t = str(ticker or "").strip().upper()
        return bool(re.fullmatch(r"[A-Z]{1,5}", t))

    def is_supported_ticker(self, ticker: str) -> bool:
        return self._is_us_ticker(ticker)

    def lookup_company(self, ticker: str) -> dict[str, Any] | None:
        normalized = str(ticker or "").strip().upper()
        if not self._is_us_ticker(normalized):
            return None
        data = self._get_json(
            self.COMPANY_TICKERS_URL,
            cache_name="sec_company_tickers.json",
            cache_ttl_hours=24,
        )
        if not isinstance(data, dict):
            return None
        for row in data.values():
            if not isinstance(row, dict):
                continue
            if str(row.get("ticker", "")).upper() == normalized:
                cik = str(row.get("cik_str", "")).strip()
                if cik:
                    return {
                        "ticker": normalized,
                        "cik": cik.zfill(10),
                        "title": row.get("title", ""),
                    }
        return None

    def get_company_profile(self, ticker: str) -> dict[str, Any]:
        company = self.lookup_company(ticker)
        if not company:
            return {
                "ticker": str(ticker or "").strip().upper(),
                "status": "unsupported_or_not_found",
                "limitations": ["SEC mapping unavailable. Non-US or unknown ticker."],
            }
        cik = company["cik"]
        submissions = self._get_json(
            self.SUBMISSIONS_URL.format(cik=cik),
            cache_name=f"sec_submissions_{cik}.json",
            cache_ttl_hours=6,
        ) or {}
        facts = self._get_json(
            self.COMPANYFACTS_URL.format(cik=cik),
            cache_name=f"sec_companyfacts_{cik}.json",
            cache_ttl_hours=6,
        ) or {}
        return {
            "ticker": company["ticker"],
            "cik": cik,
            "company_name": submissions.get("name") or company.get("title") or "",
            "sic": submissions.get("sic", ""),
            "sic_description": submissions.get("sicDescription", ""),
            "exchanges": submissions.get("exchanges", []),
            "submissions_url": self.SUBMISSIONS_URL.format(cik=cik),
            "companyfacts_url": self.COMPANYFACTS_URL.format(cik=cik),
            "recent_filings": self._recent_filings(submissions),
            "metrics": self._extract_metrics(facts),
            "status": "ok" if submissions or facts else "fetch_failed",
            "limitations": [] if self.user_agent else ["SEC_USER_AGENT is not configured; set contact info for reliable SEC access."],
        }

    def render_official_fact_sheet(self, ticker: str) -> str:
        profile = self.get_company_profile(ticker)
        if profile.get("status") == "unsupported_or_not_found":
            return (
                f"**[공식 기업 데이터: {str(ticker).upper()}]**\n"
                "- SEC EDGAR 매핑 없음: 비미국 상장사이거나 티커를 찾지 못했습니다.\n"
                "- 이 종목의 재무/공시는 거래소, DART/KRX/KIND, 기업 IR 등 별도 1차 소스 확인이 필요합니다.\n"
            )
        if profile.get("status") == "fetch_failed":
            return (
                f"**[공식 기업 데이터: {str(ticker).upper()}]**\n"
                "- SEC EDGAR 데이터 수집 실패. 네트워크, SEC_USER_AGENT, rate limit을 확인하세요.\n"
            )

        lines = [
            f"**[공식 기업 데이터: {profile.get('ticker')} | SEC EDGAR/XBRL]**",
            f"- 회사명: {profile.get('company_name') or '-'} | CIK: {profile.get('cik')}",
            f"- SIC: {profile.get('sic') or '-'} {profile.get('sic_description') or ''}".strip(),
            f"- 거래소: {', '.join(profile.get('exchanges') or []) or '-'}",
            f"- 출처: {profile.get('submissions_url')} | {profile.get('companyfacts_url')}",
        ]
        if profile.get("limitations"):
            lines.append(f"- 한계: {', '.join(profile.get('limitations', []))}")

        filings = profile.get("recent_filings") or []
        if filings:
            lines.append("- 최근 주요 제출:")
            for filing in filings[:6]:
                lines.append(
                    f"  - {filing.get('form')} filed={filing.get('filingDate')} "
                    f"period={filing.get('reportDate') or '-'} accession={filing.get('accessionNumber')}"
                )

        metrics = profile.get("metrics") or {}
        if metrics:
            lines.append("- 최신 XBRL 주요 지표:")
            for label, fact in metrics.items():
                value = self._format_value(fact.get("value"), fact.get("unit"))
                lines.append(
                    f"  - {label}: {value} | end={fact.get('end')} fy={fact.get('fy')} "
                    f"fp={fact.get('fp')} form={fact.get('form')} filed={fact.get('filed')} tag={fact.get('tag')}"
                )
        else:
            lines.append("- 최신 XBRL 주요 지표: 수집된 표준 US-GAAP 지표 없음")

        lines.append("- 주의: SEC 데이터는 공시 기반 재무/제출 정보입니다. 실시간 주가/호가/체결 가능 가격이 아닙니다.")
        return "\n".join(lines) + "\n"

    def _recent_filings(self, submissions: dict[str, Any]) -> list[dict[str, Any]]:
        recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        accessions = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []
        out = []
        wanted = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}
        for idx, form in enumerate(forms):
            if str(form) not in wanted:
                continue
            out.append(
                {
                    "form": form,
                    "filingDate": filing_dates[idx] if idx < len(filing_dates) else "",
                    "reportDate": report_dates[idx] if idx < len(report_dates) else "",
                    "accessionNumber": accessions[idx] if idx < len(accessions) else "",
                    "primaryDocument": primary_docs[idx] if idx < len(primary_docs) else "",
                }
            )
            if len(out) >= 8:
                break
        return out

    def _extract_metrics(self, facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
        us_gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
        out: dict[str, dict[str, Any]] = {}
        for label, tags in self.METRIC_TAGS.items():
            for tag in tags:
                concept = us_gaap.get(tag)
                if not isinstance(concept, dict):
                    continue
                unit_key, fact = self._latest_fact(concept)
                if fact:
                    out[label] = {
                        "tag": tag,
                        "unit": unit_key,
                        "value": fact.get("val"),
                        "end": fact.get("end"),
                        "fy": fact.get("fy"),
                        "fp": fact.get("fp"),
                        "form": fact.get("form"),
                        "filed": fact.get("filed"),
                    }
                    break
        return out

    def _latest_fact(self, concept: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        preferred_units = ["USD", "shares", "USD/shares", "pure"]
        units = concept.get("units") or {}
        unit_order = [u for u in preferred_units if u in units] + [u for u in units.keys() if u not in preferred_units]
        forms = {"10-K", "10-Q", "20-F", "40-F"}
        best_unit = ""
        best_fact = None
        best_key = ("", "")
        for unit in unit_order:
            rows = units.get(unit) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("val") is None or str(row.get("form", "")) not in forms:
                    continue
                key = (str(row.get("filed", "")), str(row.get("end", "")))
                if key >= best_key:
                    best_key = key
                    best_unit = unit
                    best_fact = row
        return best_unit, best_fact

    def _format_value(self, value: Any, unit: str | None) -> str:
        if not isinstance(value, (int, float)):
            return str(value)
        unit_text = unit or ""
        if unit_text == "USD":
            if abs(value) >= 1_000_000_000_000:
                return f"${value / 1_000_000_000_000:.2f}T"
            if abs(value) >= 1_000_000_000:
                return f"${value / 1_000_000_000:.2f}B"
            if abs(value) >= 1_000_000:
                return f"${value / 1_000_000:.2f}M"
            return f"${value:,.0f}"
        if unit_text == "shares":
            if abs(value) >= 1_000_000_000:
                return f"{value / 1_000_000_000:.2f}B shares"
            if abs(value) >= 1_000_000:
                return f"{value / 1_000_000:.2f}M shares"
            return f"{value:,.0f} shares"
        if unit_text in {"pure", "USD/shares"}:
            return f"{value:,.4g} {unit_text}".strip()
        return f"{value:,} {unit_text}".strip()
