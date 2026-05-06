import re
from datetime import datetime
from typing import Any

from db_manager import DBManager


class EvidenceMemory:
    """
    최근 뉴스 이벤트와 웹검증 evidence를 토론/RAG 친화적인 컨텍스트로 변환합니다.
    """

    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()

    def _extract_terms(self, user_query: str, extra_terms: list[str] | None = None) -> list[str]:
        terms = []
        seen = set()
        raw_terms = re.findall(
            r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b|[A-Za-z][A-Za-z0-9\-\&\.]{1,}|[가-힣]{2,}",
            user_query or "",
        )
        raw_terms.extend(extra_terms or [])
        stop = {
            "주식", "투자", "토론", "질문", "뉴스", "최근", "현재", "분석", "전망",
            "리스크", "시장", "거시", "the", "and", "for", "with",
        }
        for term in raw_terms:
            t = str(term).strip()
            if not t:
                continue
            tl = t.lower()
            if tl in stop:
                continue
            if tl not in seen:
                terms.append(t)
                seen.add(tl)
        return terms[:12]

    def collect_context(
        self,
        user_query: str,
        tickers: list[str] | None = None,
        extra_terms: list[str] | None = None,
        event_limit: int = 8,
        evidence_limit: int = 5,
        lookback_hours: int = 96,
    ) -> dict[str, Any]:
        terms = self._extract_terms(user_query, extra_terms=extra_terms)
        events = self.db.get_news_events_for_context(
            query_terms=terms,
            tickers=tickers or [],
            limit=event_limit,
            lookback_hours=lookback_hours,
        )
        research = self.db.get_recent_research_context(
            query_terms=[*terms, *(tickers or [])],
            limit=evidence_limit,
            lookback_hours=max(lookback_hours, 120),
        )
        ranked_events = sorted(
            [self._decorate_event(item, terms) for item in events],
            key=lambda x: x.get("ranking_score", 0.0),
            reverse=True,
        )
        ranked_research = sorted(
            [self._decorate_research(item, terms) for item in research],
            key=lambda x: x.get("ranking_score", 0.0),
            reverse=True,
        )
        return {
            "terms": terms,
            "events": ranked_events[:event_limit],
            "research": ranked_research[:evidence_limit],
        }

    def render_debate_brief(self, memory: dict[str, Any]) -> str:
        events = memory.get("events", []) if isinstance(memory, dict) else []
        research = memory.get("research", []) if isinstance(memory, dict) else []
        if not events and not research:
            return ""

        lines = ["[최근 근거 메모리 브리프]"]
        if events:
            lines.append("1) 최근 핵심 뉴스 이벤트")
            for idx, e in enumerate(events[:8], 1):
                matched = ", ".join(e.get("matched_terms", [])[:4]) if isinstance(e, dict) else ""
                lines.append(
                    f"- EVT{idx}: {e.get('title', '')} ({e.get('date', '')}) | "
                    f"score={e.get('ranking_score', 0)} | conf={e.get('confidence', 0)} | sources={e.get('source_count', 0)} | "
                    f"articles={e.get('article_count', 0)}"
                )
                lines.append(f"  summary={e.get('summary', '')}")
                if matched:
                    lines.append(f"  matched_terms={matched}")
                for url in (e.get("sample_urls", []) or [])[:2]:
                    lines.append(f"  source={url}")

        if research:
            lines.append("2) 최근 웹검증 evidence")
            for idx, item in enumerate(research[:5], 1):
                lines.append(
                    f"- RES{idx}: topic={item.get('topic', '')} | query={item.get('query', '')} | "
                    f"status={item.get('status', '')} | evidence_count={item.get('evidence_count', 0)} | "
                    f"tier={','.join(item.get('source_tiers', [])[:2]) or '-'} | score={item.get('ranking_score', 0)}"
                )
                summary = str(item.get("summary", "")).strip()
                if summary:
                    lines.append(f"  summary={summary[:500]}")
                for src in (item.get("sources", []) or [])[:2]:
                    lines.append(
                        f"  source={src.get('title', '')} ({src.get('domain', '')}) {src.get('url', '')}"
                    )
        return "\n".join(lines)

    def render_rag_context(self, memory: dict[str, Any]) -> list[str]:
        events = memory.get("events", []) if isinstance(memory, dict) else []
        research = memory.get("research", []) if isinstance(memory, dict) else []
        contexts: list[str] = []
        for e in events[:5]:
            contexts.append(
                f"[최근 뉴스 이벤트 {e.get('date', '')}] {e.get('title', '')}\n"
                f"- 요약: {e.get('summary', '')}\n"
                f"- 신뢰: conf={e.get('confidence', 0)}, sources={e.get('source_count', 0)}, articles={e.get('article_count', 0)}"
            )
        for item in research[:4]:
            contexts.append(
                f"[최근 웹검증 {item.get('created_at', '')}] query={item.get('query', '')}\n"
                f"- topic: {item.get('topic', '')}\n"
                f"- 요약: {str(item.get('summary', '')).strip()[:500]}\n"
                f"- evidence_count: {item.get('evidence_count', 0)}\n"
                f"- source_tiers: {', '.join(item.get('source_tiers', [])[:3]) or '-'}"
            )
        return contexts

    def _decorate_event(self, event: dict[str, Any], terms: list[str]) -> dict[str, Any]:
        item = dict(event or {})
        confidence = float(item.get("confidence", 0.0) or 0.0)
        source_count = int(item.get("source_count", 0) or 0)
        article_count = int(item.get("article_count", 0) or 0)
        matched = len(item.get("matched_terms", []) or [])
        recency_bonus = self._recency_bonus(item.get("date"))
        ranking_score = round((confidence * 50.0) + (source_count * 4.0) + (article_count * 2.0) + (matched * 6.0) + recency_bonus, 2)
        item["ranking_score"] = ranking_score
        return item

    def _decorate_research(self, research: dict[str, Any], terms: list[str]) -> dict[str, Any]:
        item = dict(research or {})
        evidence_count = int(item.get("evidence_count", 0) or 0)
        quality_avg = float(item.get("source_quality_avg", 0.0) or 0.0)
        tier_bonus = len(item.get("source_tiers", []) or []) * 2.0
        query_text = f"{item.get('topic', '')} {item.get('query', '')}".lower()
        term_hits = sum(1 for t in terms if str(t).lower() in query_text)
        recency_bonus = self._recency_bonus(item.get("created_at"))
        ranking_score = round((evidence_count * 8.0) + (quality_avg * 10.0) + tier_bonus + (term_hits * 4.0) + recency_bonus, 2)
        item["ranking_score"] = ranking_score
        return item

    def _recency_bonus(self, value: str | None) -> float:
        if not value:
            return 0.0
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
        except Exception:
            try:
                dt = datetime.strptime(str(value), "%Y-%m-%d")
            except Exception:
                return 0.0
        hours = max(0.0, (datetime.now() - dt).total_seconds() / 3600.0)
        return max(0.0, 12.0 - min(hours, 48.0) / 4.0)
