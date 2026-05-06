from __future__ import annotations

import argparse
import json
import os
from typing import Any

from db_manager import DBManager
from news_context_pack import NewsContextPackService


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace("\n", ";").split(";") if item.strip()]


def _default_queries_from_recent_events(db: DBManager, limit: int) -> list[str]:
    events = db.get_latest_news_events(limit=limit)
    queries = []
    for event in events:
        title = str(event.get("title", "")).strip()
        if title:
            queries.append(title)
    return queries


def build_context_packs(args: argparse.Namespace) -> list[dict[str, Any]]:
    db = DBManager()
    service = NewsContextPackService(db)
    queries = list(args.queries)
    queries.extend(_split_env_list(os.getenv("NEWS_CONTEXT_QUERIES")))
    if not queries:
        queries = _default_queries_from_recent_events(db, args.default_recent_events)

    packs = []
    for query in dict.fromkeys(q for q in queries if q.strip()):
        pack = service.build_for_query(
            query=query,
            tickers=args.ticker,
            extra_terms=args.term,
            event_limit=args.event_limit,
            article_limit_per_event=args.article_limit,
            evidence_limit=args.evidence_limit,
            lookback_hours=args.lookback_hours,
            persist=not args.no_persist,
        )
        packs.append(pack)
    return packs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build independent news context packs without starting the group-chat debate bot.",
    )
    parser.add_argument("queries", nargs="*", help="Investment/news queries to build context packs for.")
    parser.add_argument("--ticker", action="append", default=[], help="Ticker to prioritize. Repeatable.")
    parser.add_argument("--term", action="append", default=[], help="Extra term to prioritize. Repeatable.")
    parser.add_argument("--event-limit", type=int, default=8)
    parser.add_argument("--article-limit", type=int, default=5)
    parser.add_argument("--evidence-limit", type=int, default=5)
    parser.add_argument("--lookback-hours", type=int, default=168)
    parser.add_argument("--default-recent-events", type=int, default=5)
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    packs = build_context_packs(args)
    summary = {
        "pack_count": len(packs),
        "packs": [
            {
                "query_hash": pack.get("query_hash"),
                "query": pack.get("query"),
                "generated_at": pack.get("generated_at"),
                "quality": pack.get("quality"),
                "recommended_web_queries": pack.get("recommended_web_queries", [])[:5],
            }
            for pack in packs
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
