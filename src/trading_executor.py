from datetime import datetime, timedelta

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

from db_manager import DBManager
from market_data_provider import MarketDataProvider
from paper_broker import PaperBroker
from risk_manager import RiskManager
from yfinance_runtime import configure_yfinance_cache


class TradingExecutor:
    """
    승인된 시그널을 페이퍼 트레이딩으로 체결합니다.
    """

    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()
        self.broker = PaperBroker(self.db)
        self.risk_manager = RiskManager(self.db)
        self.market_data = MarketDataProvider()
        configure_yfinance_cache(yf)

    def _fetch_price(self, ticker: str) -> float:
        quote = self.market_data.get_latest_quote(ticker)
        return float(quote.price) if quote else 0.0

    def _fetch_price_with_quality(self, ticker: str) -> tuple[float, dict]:
        quote = self.market_data.get_latest_quote(ticker)
        if quote:
            return float(quote.price), self.market_data.assess_quote_quality(quote)
        px = self._fetch_price(ticker)
        if px > 0:
            return px, {"state": "reference", "tradable": True, "source": "override_or_test_provider"}
        return 0.0, {"state": "missing", "tradable": False, "reasons": ["missing_quote"]}

    def _fetch_market_context(self, ticker: str) -> dict[str, float]:
        if yf is None:
            return {}
        try:
            instrument = self.market_data.resolve_instrument(ticker)
            provider_ticker = instrument.provider_ticker if instrument else str(ticker or "").upper().strip()
            hist = yf.Ticker(provider_ticker).history(period="1mo", interval="1d")
            if getattr(hist, "empty", True) or len(hist) < 6:
                return {}
            closes = hist["Close"].dropna().tolist()
            if len(closes) < 6:
                return {}
            returns = []
            for prev, cur in zip(closes[:-1], closes[1:]):
                if prev:
                    returns.append(((float(cur) - float(prev)) / float(prev)) * 100.0)
            if not returns:
                return {}
            mean_abs = sum(abs(r) for r in returns[-10:]) / min(len(returns), 10)
            return {"volatility_pct": round(mean_abs, 4)}
        except Exception:
            return {}

    def _check_guardrail(self) -> tuple[bool, str]:
        state = self.db.get_guardrail_state()
        if state.get("kill_switch", True):
            return False, "kill_switch ON (자동매매 중지 상태)"

        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')
        hour_start = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S')
        daily_cnt = self.db.count_orders_in_window(day_start)
        hourly_cnt = self.db.count_orders_in_window(hour_start)
        if daily_cnt >= int(state.get("daily_order_limit", 0)):
            return False, f"daily_order_limit 초과 ({daily_cnt})"
        if hourly_cnt >= int(state.get("hourly_order_limit", 0)):
            return False, f"hourly_order_limit 초과 ({hourly_cnt})"
        return True, "ok"

    def execute_paper(self, event_id: str, approved_by: str) -> tuple[bool, str]:
        ok, reason = self._check_guardrail()
        if not ok:
            return False, f"❌ 실행 차단: {reason}"

        approval = self.db.get_approval_request(event_id)
        if not approval:
            return False, f"❌ 승인 요청이 없습니다: `{event_id}`"
        if approval.get("state") not in {"approved", "pending"}:
            return False, f"❌ 승인 상태가 아닙니다: {approval.get('state')}"
        expires_at = approval.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) < datetime.now():
                    return False, f"❌ 승인 만료됨: `{event_id}`"
            except Exception:
                pass

        recs = self.db.get_recommendations(event_id)
        actionable = [r for r in recs if r.get("status") == "pending_approval"]
        if not actionable:
            return False, f"❌ 실행 가능한 추천이 없습니다: `{event_id}`"

        prices: dict[str, float] = {}
        market_contexts: dict[str, dict[str, float]] = {}
        for r in actionable:
            ticker = r.get("ticker", "")
            px, quality = self._fetch_price_with_quality(ticker)
            if px <= 0:
                return False, f"❌ 가격 조회 실패로 실행 중단: `{ticker}`"
            if str(quality.get("state", "")).lower() in {"missing", "stale"}:
                return False, f"market data quality gate failed: `{ticker}` ({quality})"
            prices[ticker] = px
            market_contexts[ticker] = self._fetch_market_context(ticker)

        risk_ok, risk_reason, execution_plan = self.risk_manager.evaluate_event(
            event_id=event_id,
            recommendations=actionable,
            prices=prices,
            market_contexts=market_contexts,
        )
        if not risk_ok:
            return False, risk_reason

        self.broker.refresh_market_prices(prices)

        if approval.get("state") == "pending":
            self.db.approve_request(event_id, approved_by, note="즉시 승인+체결")

        for item in execution_plan:
            rec = next(
                (r for r in actionable if str(r.get("ticker", "")).upper().strip() == item["ticker"] and str(r.get("side", "BUY")).upper().strip() == item["side"]),
                {},
            )
            signal_event = self.db.get_signal_event(event_id) or {}
            self.broker.submit_market_order(
                event_id=event_id,
                ticker=item["ticker"],
                side=item["side"],
                qty=item["qty"],
                fill_price=item["price"],
                order_type="paper_market",
                notes=f"approved_by={approved_by}",
                detail_json={
                    "approved_by": approved_by,
                    "price_source": self.market_data.provider,
                    "market_data_quality": "paper_reference_not_execution_grade",
                    "rationale": item.get("rationale", ""),
                    "confidence": item.get("confidence", 0.0),
                    "size_rule": item.get("size_rule", ""),
                    "notional": item.get("notional", 0.0),
                    "entry_rule": rec.get("entry_rule", ""),
                    "stop_rule": rec.get("stop_rule", ""),
                    "ttl_sec": rec.get("ttl_sec", 0),
                    "urgency": signal_event.get("urgency", ""),
                    "volatility_pct": item.get("volatility_pct", 0.0),
                },
            )

        self.db.set_recommendations_status(event_id, "executed")
        self.db.mark_approval_executed(event_id, note=f"paper executed by {approved_by}")
        self.db.set_signal_event_status(event_id, "executed")
        return True, f"✅ 페이퍼 체결 완료: `{event_id}` ({len(execution_plan)}건)"
