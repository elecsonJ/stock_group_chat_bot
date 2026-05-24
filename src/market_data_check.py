from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Any

from db_manager import DBManager
from market_data_provider import MarketDataProvider


DEFAULT_TICKERS = ["NVDA", "005930", "KOSDAQ:035720", "HK:700", "JP:7203"]


class MarketDataConnectivityCheck:
    def __init__(self, db: DBManager | None = None, market_data: MarketDataProvider | None = None):
        self.db = db or DBManager()
        self.market_data = market_data or MarketDataProvider()

    def run(self, tickers: list[str] | None = None) -> dict[str, Any]:
        requested = tickers or self._env_tickers() or DEFAULT_TICKERS
        rows = [self._check_one(ticker) for ticker in requested]
        missing = [row for row in rows if row.get("status") == "fail"]
        warn = [row for row in rows if row.get("status") == "warn"]
        overall = "fail" if missing else ("warn" if warn else "ok")
        report = {
            "schema_version": "market_data_connectivity.v1",
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": self.market_data.provider,
            "overall_status": overall,
            "checks": rows,
        }
        self.db.set_system_metadata("market_data_connectivity_report_v1", json.dumps(report, ensure_ascii=False))
        return report

    def render(self, report: dict[str, Any]) -> str:
        lines = [
            "Market Data Connectivity Check",
            f"- provider: {report.get('provider')}",
            f"- overall: {report.get('overall_status')}",
            f"- generated_at: {report.get('generated_at')}",
        ]
        for row in report.get("checks", []):
            detail = row.get("detail", {})
            lines.append(
                "- "
                f"{row.get('requested_ticker')} -> {row.get('provider_ticker') or '-'} "
                f"[{row.get('market') or 'unknown'}] {row.get('status')} "
                f"price={detail.get('price', '-')}"
            )
        return "\n".join(lines)

    def _check_one(self, ticker: str) -> dict[str, Any]:
        instrument = self.market_data.resolve_instrument(ticker)
        quote = self.market_data.get_latest_quote(ticker)
        quality = self.market_data.assess_quote_quality(quote, max_age_minutes=24 * 60)
        status = "ok"
        if quality.get("state") == "missing":
            status = "fail"
        elif not quality.get("tradable", False):
            status = "warn"
        detail = quality | {
            "currency": quote.currency if quote else (instrument.currency_hint if instrument else ""),
            "country": (quote.detail or {}).get("country") if quote else (instrument.country if instrument else ""),
            "exchange_hint": (quote.detail or {}).get("exchange_hint") if quote else (instrument.exchange_hint if instrument else ""),
            "benchmark_ticker": (quote.detail or {}).get("benchmark_ticker") if quote else (instrument.benchmark_ticker if instrument else ""),
            "reasons": quality.get("reasons", []),
        }
        return {
            "requested_ticker": ticker,
            "provider_ticker": quote.ticker if quote else (instrument.provider_ticker if instrument else ""),
            "market": (quote.detail or {}).get("market") if quote else (instrument.market if instrument else "unknown"),
            "status": status,
            "detail": detail,
        }

    def _env_tickers(self) -> list[str]:
        raw = os.getenv("MARKET_DATA_CHECK_TICKERS", "")
        return [part.strip() for part in raw.split(",") if part.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="", help="Comma-separated tickers such as NVDA,005930,HK:700,JP:7203")
    args = parser.parse_args()
    tickers = [part.strip() for part in args.tickers.split(",") if part.strip()]
    checker = MarketDataConnectivityCheck()
    report = checker.run(tickers=tickers or None)
    print(checker.render(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("overall_status") == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
