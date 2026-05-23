from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Any

from db_manager import DBManager
from market_data_provider import MarketDataProvider
from reconciliation import PaperStateReconciler


class LiveReadinessChecker:
    def __init__(self, db: DBManager | None = None, market_data: MarketDataProvider | None = None):
        self.db = db or DBManager()
        self.market_data = market_data or MarketDataProvider()

    def run(self, *, ticker: str = "SPY", include_network: bool = False) -> dict[str, Any]:
        checks = []
        checks.append(self._check_db())
        checks.append(self._check_guardrail())
        checks.append(self._check_env())
        checks.append(self._check_reconciliation())
        checks.append(self._check_recent_data())
        if include_network:
            checks.append(self._check_market_data(ticker))
        else:
            checks.append({"name": "market_data_network", "status": "skipped", "detail": {"reason": "include_network=false"}})
        failed = [c for c in checks if c.get("status") == "fail"]
        warn = [c for c in checks if c.get("status") == "warn"]
        overall = "fail" if failed else ("warn" if warn else "ok")
        report = {
            "schema_version": "live_readiness.v1",
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "overall_status": overall,
            "checks": checks,
            "go_live_allowed": False,
            "note": "This checker never enables live trading; it only reports readiness blockers.",
        }
        self.db.set_system_metadata("live_readiness_report_v1", json.dumps(report, ensure_ascii=False))
        return report

    def render(self, report: dict[str, Any]) -> str:
        lines = [
            "Live Readiness Check",
            f"- overall: {report.get('overall_status')}",
            f"- generated_at: {report.get('generated_at')}",
            f"- go_live_allowed: {report.get('go_live_allowed')}",
        ]
        for check in report.get("checks", []):
            lines.append(f"- {check.get('name')}: {check.get('status')} | {check.get('detail')}")
        return "\n".join(lines)

    def _check_db(self) -> dict[str, Any]:
        try:
            self.db.get_guardrail_state()
            return {"name": "db", "status": "ok", "detail": {"path": self.db.db_path}}
        except Exception as exc:
            return {"name": "db", "status": "fail", "detail": {"error": str(exc)}}

    def _check_guardrail(self) -> dict[str, Any]:
        state = self.db.get_guardrail_state()
        if state.get("kill_switch", True):
            return {"name": "kill_switch", "status": "ok", "detail": {"kill_switch": "ON"}}
        return {"name": "kill_switch", "status": "warn", "detail": {"kill_switch": "OFF", "reason": "live should default locked"}}

    def _check_env(self) -> dict[str, Any]:
        missing = []
        if not os.getenv("DISCORD_TOKEN"):
            missing.append("DISCORD_TOKEN")
        if not os.getenv("LOCAL_MODEL_NAME"):
            missing.append("LOCAL_MODEL_NAME")
        status = "warn" if missing else "ok"
        return {"name": "env", "status": status, "detail": {"missing": missing}}

    def _check_reconciliation(self) -> dict[str, Any]:
        report = PaperStateReconciler(self.db).run(persist=True)
        status = "ok" if report.get("status") == "ok" else "fail"
        return {
            "name": "paper_reconciliation",
            "status": status,
            "detail": {"mismatch_count": report.get("mismatch_count", 0)},
        }

    def _check_recent_data(self) -> dict[str, Any]:
        events = self.db.get_latest_news_events(limit=5)
        snapshots = self.db.list_market_reaction_snapshots(limit=5)
        detail = {
            "recent_news_events": len(events),
            "recent_market_reaction_snapshots": len(snapshots),
        }
        if not events:
            return {"name": "recent_news", "status": "warn", "detail": detail}
        if not snapshots:
            return {"name": "market_reaction_snapshots", "status": "warn", "detail": detail}
        return {"name": "recent_data", "status": "ok", "detail": detail}

    def _check_market_data(self, ticker: str) -> dict[str, Any]:
        quote = self.market_data.get_latest_quote(ticker)
        quality = self.market_data.assess_quote_quality(quote)
        status = "ok" if quality.get("state") in {"reference"} else "warn"
        if quality.get("state") == "missing":
            status = "fail"
        return {"name": "market_data", "status": status, "detail": quality}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default=os.getenv("READINESS_TICKER", "SPY"))
    parser.add_argument("--include-network", action="store_true")
    args = parser.parse_args()
    checker = LiveReadinessChecker()
    report = checker.run(ticker=args.ticker, include_network=args.include_network)
    print(checker.render(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("overall_status") == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
