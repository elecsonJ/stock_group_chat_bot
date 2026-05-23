from __future__ import annotations

import argparse

from db_manager import DBManager
from market_reaction import MarketReactionAnalyzer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--benchmark", default="SPY")
    args = parser.parse_args()

    db = DBManager()
    analyzer = MarketReactionAnalyzer(db)
    event_ids = []
    if args.event_id:
        event_ids = [args.event_id.strip().upper()]
    else:
        events = db.list_recent_signal_events(limit=args.limit)
        event_ids = [str(e.get("event_id") or "").strip() for e in events if e.get("event_id")]

    total = 0
    for event_id in event_ids:
        rows = analyzer.capture_for_signal_event(event_id, benchmark_ticker=args.benchmark, persist=True)
        total += len(rows)
        print(f"{event_id}: captured={len(rows)}")
    print(f"total_snapshots={total}")


if __name__ == "__main__":
    main()
