import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db_manager import DBManager
from llm_client import LLMClientManager
from portfolio_manager import PortfolioManager
from signal_engine import SignalEngine
from stable_web_search_agent import FactCheckAgent


async def main():
    db = DBManager()
    pm = PortfolioManager()
    engine = SignalEngine(db)
    llm = LLMClientManager()
    checker = FactCheckAgent(llm)
    threshold = float(os.getenv("SIGNAL_MIN_SCORE", "58"))
    max_events = int(os.getenv("SIGNAL_MAX_EVENTS", "20"))
    verify_new_only = os.getenv("SIGNAL_VERIFY_NEW_ONLY", "true").strip().lower() not in {"0", "false", "no"}

    raw = pm.load_raw_portfolio() or ""
    holdings, _ = pm.parse_holdings(raw) if raw else ([], [])
    holdings_agg = pm.aggregate_holdings(holdings)
    portfolio_tickers = [h["ticker"] for h in holdings_agg]

    created = await engine.generate_signals_from_news(
        portfolio_tickers=portfolio_tickers,
        max_events=max_events,
        threshold=threshold,
        checker=checker,
        verify_new_only=verify_new_only,
    )
    pending = db.list_pending_approvals(limit=20)
    debate_pending = db.list_debate_queue(limit=20, statuses=["pending"])
    review_open = db.list_investment_review_triggers(limit=20, statuses=["open"])

    print("=== 단기 시그널 배치 ===")
    print(f"- created: {len(created)}")
    print(f"- new: {sum(1 for c in created if c.get('new'))}")
    print(f"- pending: {len(pending)}")
    print(f"- debate_queue_pending: {len(debate_pending)}")
    print(f"- review_triggers_open: {len(review_open)}")
    if pending:
        for p in pending[:10]:
            print(f"  * {p['event_id']} score={p['score_total']:.1f} expires={p.get('expires_at')}")


if __name__ == "__main__":
    asyncio.run(main())
