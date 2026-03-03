import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(__file__))


class PortfolioManager:
    def __init__(self):
        env_path = os.getenv("PORTFOLIO_FILE_PATH", "").strip()
        if env_path:
            self.file_path = env_path if os.path.isabs(env_path) else os.path.join(_project_root(), env_path)
        else:
            self.file_path = os.path.join(_project_root(), "data", "my_portfolio.md")

    def load_raw_portfolio(self) -> str | None:
        if not os.path.exists(self.file_path):
            return None
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def parse_holdings(self, raw_text: str) -> tuple[list[dict[str, Any]], list[str]]:
        if not raw_text:
            return [], ["포트폴리오 파일 내용이 비어 있습니다."]

        parsed, warnings = self._parse_json_block(raw_text)
        if parsed:
            return parsed, warnings

        holdings: list[dict[str, Any]] = []
        for line in raw_text.splitlines():
            h = self._parse_holding_line(line)
            if h:
                holdings.append(h)

        if not holdings:
            warnings.append(
                "파싱 가능한 보유 라인이 없습니다. "
                "예시: `NVDA | qty: 3 | avg: 780` 또는 `005930.KS, 12, 71200`"
            )
        return holdings, warnings

    def _parse_json_block(self, raw_text: str) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        m = re.search(r"```portfolio-json\s*(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return [], warnings
        body = m.group(1).strip()
        if not body:
            warnings.append("portfolio-json 블록이 비어 있습니다.")
            return [], warnings
        try:
            payload = json.loads(body)
        except Exception as e:
            warnings.append(f"portfolio-json 파싱 실패: {e}")
            return [], warnings

        if isinstance(payload, dict):
            rows = payload.get("holdings", [])
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        holdings: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            ticker = self._normalize_ticker(str(item.get("ticker", "")).strip())
            qty = self._to_float(item.get("qty"))
            avg = self._to_float(item.get("avg_price"))
            if not ticker or qty is None or avg is None:
                continue
            currency = str(item.get("currency", "")).upper().strip() or self._infer_currency(ticker, "")
            holdings.append(
                {
                    "ticker": ticker,
                    "qty": qty,
                    "avg_price": avg,
                    "currency": currency,
                    "note": str(item.get("note", "")).strip(),
                }
            )
        return holdings, warnings

    def _parse_holding_line(self, line: str) -> dict[str, Any] | None:
        text = line.strip()
        if not text:
            return None
        if text.startswith("#") or text.startswith("//"):
            return None
        text = re.sub(r"^\s*[-*]\s*", "", text)
        text = re.sub(r"^\s*\d+\.\s+", "", text)

        patterns = [
            r"(?P<ticker>[A-Za-z0-9\.]{1,12}).*?(?:qty|수량)\s*[:=]\s*(?P<qty>-?\d+(?:\.\d+)?).*?(?:avg|평단|매입가)\s*[:=]\s*(?P<avg>\d+(?:\.\d+)?)",
            r"(?P<ticker>[A-Za-z0-9\.]{1,12})\s*[|,]\s*(?P<qty>-?\d+(?:\.\d+)?)\s*[|,]\s*(?P<avg>\d+(?:\.\d+)?)",
            r"(?P<ticker>[A-Za-z0-9\.]{1,12})\s+(?P<qty>-?\d+(?:\.\d+)?)\s*(?:주|shares?)?\s*@\s*(?P<avg>\d+(?:\.\d+)?)",
            r"(?P<ticker>[A-Za-z0-9\.]{1,12})\s+(?P<qty>-?\d+(?:\.\d+)?)\s+(?P<avg>\d+(?:\.\d+)?)$",
        ]
        for p in patterns:
            m = re.search(p, text, flags=re.IGNORECASE)
            if not m:
                continue
            ticker = self._normalize_ticker(m.group("ticker"))
            qty = self._to_float(m.group("qty"))
            avg = self._to_float(m.group("avg"))
            if not ticker or qty is None or avg is None:
                continue
            currency = self._infer_currency(ticker, text)
            return {
                "ticker": ticker,
                "qty": qty,
                "avg_price": avg,
                "currency": currency,
                "note": "",
            }
        return None

    def aggregate_holdings(self, holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bucket: dict[tuple[str, str], dict[str, Any]] = {}
        for h in holdings:
            ticker = str(h.get("ticker", "")).upper().strip()
            currency = str(h.get("currency", "")).upper().strip() or "USD"
            qty = self._to_float(h.get("qty"))
            avg = self._to_float(h.get("avg_price"))
            if not ticker or qty is None or avg is None or qty == 0:
                continue
            key = (ticker, currency)
            if key not in bucket:
                bucket[key] = {"ticker": ticker, "currency": currency, "qty": 0.0, "cost": 0.0}
            bucket[key]["qty"] += qty
            bucket[key]["cost"] += qty * avg

        out: list[dict[str, Any]] = []
        for row in bucket.values():
            qty = row["qty"]
            if qty == 0:
                continue
            avg_price = row["cost"] / qty
            out.append(
                {
                    "ticker": row["ticker"],
                    "currency": row["currency"],
                    "qty": qty,
                    "avg_price": avg_price,
                }
            )
        return sorted(out, key=lambda x: x["ticker"])

    def build_llm_context(self, holdings: list[dict[str, Any]], raw_text: str = "") -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not holdings:
            excerpt = (raw_text or "").strip()[:1200]
            return (
                f"[포트폴리오 스냅샷 {now}]\n"
                "파싱 가능한 보유 종목이 없어 원문 일부만 제공합니다.\n"
                f"{excerpt}"
            )

        lines = [f"[포트폴리오 스냅샷 {now}]"]
        for h in holdings:
            lines.append(
                f"- {h['ticker']} | qty={h['qty']:.4g} | avg={h['avg_price']:.4f} | currency={h['currency']}"
            )
        lines.append("주의: 투자 판단 시 이 포트폴리오의 기존 보유/집중도 리스크를 함께 고려하라.")
        return "\n".join(lines)

    async def get_variation_snapshot(self, holdings: list[dict[str, Any]]) -> dict[str, Any]:
        if yf is None:
            return {"status": "error", "message": "yfinance 미설치", "rows": []}
        if not holdings:
            return {"status": "empty", "rows": []}

        async def fetch_price(ticker: str) -> dict[str, Any]:
            def _fetch():
                info = yf.Ticker(ticker).info
                price = info.get("currentPrice") or info.get("regularMarketPrice")
                currency = info.get("currency") or ""
                return {"price": price, "currency": currency}
            try:
                data = await asyncio.to_thread(_fetch)
                return {"ticker": ticker, **data}
            except Exception as e:
                return {"ticker": ticker, "error": str(e)}

        tasks = [fetch_price(h["ticker"]) for h in holdings]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        price_map = {r.get("ticker"): r for r in results if isinstance(r, dict)}

        rows = []
        by_currency: dict[str, dict[str, float]] = {}
        for h in holdings:
            ticker = h["ticker"]
            qty = float(h["qty"])
            avg = float(h["avg_price"])
            cost = qty * avg
            cur = h["currency"]
            px_row = price_map.get(ticker, {})
            current = self._to_float(px_row.get("price"))
            if current is None:
                rows.append(
                    {
                        "ticker": ticker,
                        "currency": cur,
                        "qty": qty,
                        "avg_price": avg,
                        "status": "가격 조회 실패",
                    }
                )
                continue
            value = qty * current
            pnl = value - cost
            pnl_pct = (pnl / cost * 100.0) if cost else 0.0
            rows.append(
                {
                    "ticker": ticker,
                    "currency": cur,
                    "qty": qty,
                    "avg_price": avg,
                    "current_price": current,
                    "cost": cost,
                    "value": value,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "status": "ok",
                }
            )
            agg = by_currency.setdefault(cur, {"cost": 0.0, "value": 0.0})
            agg["cost"] += cost
            agg["value"] += value

        return {"status": "ok", "rows": rows, "summary_by_currency": by_currency}

    def render_portfolio_text(self, raw_text: str, holdings: list[dict[str, Any]], warnings: list[str]) -> str:
        lines = [f"📦 **[포트폴리오 로드]** `{self.file_path}`"]
        if not raw_text:
            lines.append("파일 내용을 읽지 못했습니다.")
            return "\n".join(lines)

        lines.append(f"- 파싱 보유건수: {len(holdings)}")
        if warnings:
            lines.append(f"- 경고: {' | '.join(warnings[:3])}")
        if holdings:
            lines.append("**[파싱 결과]**")
            for h in holdings:
                lines.append(
                    f"- `{h['ticker']}` qty={h['qty']:.4g} avg={h['avg_price']:.4f} {h['currency']}"
                )
        else:
            lines.append("**[원문 일부]**")
            lines.append(f"```text\n{raw_text[:1200]}\n```")
        return "\n".join(lines)

    def render_variation_text(self, snapshot: dict[str, Any]) -> str:
        status = snapshot.get("status")
        if status == "error":
            return f"⚠️ 포트폴리오 변동 계산 실패: {snapshot.get('message', 'unknown')}"
        if status == "empty":
            return "ℹ️ 파싱 가능한 보유 종목이 없어 변동 계산을 건너뜁니다."

        rows = snapshot.get("rows", [])
        if not rows:
            return "ℹ️ 변동 계산 결과가 없습니다."

        lines = ["📊 **[포트폴리오 변동 스냅샷]**"]
        for r in rows:
            if r.get("status") != "ok":
                lines.append(f"- `{r['ticker']}`: 가격 조회 실패")
                continue
            lines.append(
                f"- `{r['ticker']}` {r['currency']} | qty={r['qty']:.4g} | avg={r['avg_price']:.4f} | "
                f"now={r['current_price']:.4f} | PnL={r['pnl']:.2f} ({r['pnl_pct']:.2f}%)"
            )

        summary = snapshot.get("summary_by_currency", {})
        if summary:
            lines.append("**[통화별 합계]**")
            for cur, s in summary.items():
                cost = float(s.get("cost", 0.0))
                value = float(s.get("value", 0.0))
                pnl = value - cost
                pct = (pnl / cost * 100.0) if cost else 0.0
                lines.append(f"- {cur}: cost={cost:.2f}, value={value:.2f}, PnL={pnl:.2f} ({pct:.2f}%)")
        return "\n".join(lines)

    def _normalize_ticker(self, raw: str) -> str:
        t = (raw or "").strip().upper()
        t = re.sub(r"[^A-Z0-9\.]", "", t)
        if not t:
            return ""
        if re.fullmatch(r"\d{6}", t):
            return f"{t}.KS"
        return t

    def _infer_currency(self, ticker: str, line: str) -> str:
        low = (line or "").lower()
        if "krw" in low or "원" in line:
            return "KRW"
        if "usd" in low or "$" in line or "달러" in line:
            return "USD"
        if ticker.endswith(".KS") or ticker.endswith(".KQ"):
            return "KRW"
        return "USD"

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip().replace(",", "")
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return None
        try:
            return float(m.group(0))
        except Exception:
            return None
