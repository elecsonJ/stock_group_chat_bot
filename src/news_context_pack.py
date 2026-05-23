from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from db_manager import DBManager


class NewsContextPackService:
    """
    뉴스 기억 계층의 독립 인터페이스.

    LLM, Discord, 토론 매니저에 의존하지 않고 DB에 쌓인 뉴스/검증 근거를
    판단용 Context Pack으로 정리한다.
    """

    SCHEMA_VERSION = "news_context_pack.v1"

    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()

    def build_for_query(
        self,
        *,
        query: str,
        tickers: list[str] | None = None,
        extra_terms: list[str] | None = None,
        event_limit: int = 8,
        article_limit_per_event: int = 5,
        evidence_limit: int = 5,
        lookback_hours: int = 168,
        persist: bool = True,
    ) -> dict[str, Any]:
        terms = self.extract_terms(query, extra_terms=extra_terms, tickers=tickers)
        events = self.db.get_news_events_for_context(
            query_terms=terms,
            tickers=tickers or [],
            limit=event_limit,
            lookback_hours=lookback_hours,
        )
        event_keys = [str(e.get("event_key", "")).strip() for e in events if e.get("event_key")]
        articles_by_event = self.db.list_news_articles_for_events(
            event_keys,
            limit_per_event=article_limit_per_event,
        )
        research = self.db.get_recent_research_context(
            query_terms=[*terms, *(tickers or [])],
            limit=evidence_limit,
            lookback_hours=max(lookback_hours, 120),
        )

        decorated_events = [
            self._decorate_event(event, articles_by_event.get(str(event.get("event_key", "")), []), terms)
            for event in events
        ]
        decorated_research = [
            self._decorate_research(item, terms)
            for item in research
        ]
        quality = self._assess_pack_quality(decorated_events, decorated_research)
        web_queries = self._recommend_web_queries(query, tickers or [], decorated_events, quality)

        pack = {
            "schema_version": self.SCHEMA_VERSION,
            "query_hash": self.query_hash(query, tickers=tickers, extra_terms=extra_terms),
            "query": query,
            "tickers": list(dict.fromkeys([str(t).strip().upper() for t in (tickers or []) if str(t).strip()])),
            "terms": terms,
            "generated_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            "events": decorated_events,
            "research": decorated_research,
            "quality": quality,
            "recommended_web_queries": web_queries,
        }
        if persist:
            self._save_context_audit(
                pack=pack,
                consumer="news_context_pack",
                selected_events=decorated_events,
                selected_research=decorated_research,
                event_limit=event_limit,
                evidence_limit=evidence_limit,
                lookback_hours=lookback_hours,
            )
        if persist:
            self.db.save_news_context_pack(pack["query_hash"], query, pack)
        return pack

    def extract_terms(
        self,
        query: str,
        *,
        extra_terms: list[str] | None = None,
        tickers: list[str] | None = None,
    ) -> list[str]:
        raw_terms = re.findall(
            r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b|[A-Za-z][A-Za-z0-9\-\&\.]{1,}|[가-힣]{2,}",
            query or "",
        )
        raw_terms.extend(extra_terms or [])
        raw_terms.extend(tickers or [])
        stop = {
            "주식", "투자", "토론", "질문", "뉴스", "최근", "현재", "분석", "전망",
            "리스크", "시장", "거시", "the", "and", "for", "with", "latest",
            "stock", "market", "company", "impact",
        }
        out = []
        seen = set()
        for term in raw_terms:
            t = str(term or "").strip()
            if not t:
                continue
            tl = t.lower()
            if tl in stop:
                continue
            if tl not in seen:
                out.append(t)
                seen.add(tl)
        return out[:14]

    def render_for_model(self, pack: dict[str, Any]) -> str:
        if not pack:
            return ""
        lines = ["[NEWS_CONTEXT_PACK]"]
        q = pack.get("quality", {}) if isinstance(pack, dict) else {}
        lines.append(
            f"state={q.get('state', 'unknown')} score={q.get('score', 0)} "
            f"web_required={q.get('web_required', False)}"
        )
        limitations = q.get("limitations", []) or []
        if limitations:
            lines.append(f"limitations={', '.join(limitations[:6])}")

        events = pack.get("events", []) or []
        if events:
            lines.append("1) Local event memory")
            for idx, event in enumerate(events[:8], 1):
                lines.append(
                    f"- E{idx}: {event.get('title', '')} ({event.get('date', '')}) | "
                    f"rank={event.get('ranking_score', 0)} | status={event.get('memory_status', '')} | "
                    f"sources={event.get('source_count', 0)} articles={event.get('article_count', 0)}"
                )
                lines.append(f"  summary={event.get('summary', '')}")
                source_mix = event.get("source_mix", {})
                if source_mix:
                    lines.append(f"  source_mix={source_mix}")
                for article in (event.get("articles", []) or [])[:2]:
                    lines.append(
                        f"  source={article.get('source', '')} tier={article.get('source_tier', '')} "
                        f"title={article.get('title', '')} url={article.get('canonical_url') or article.get('url', '')}"
                    )

        research = pack.get("research", []) or []
        if research:
            lines.append("2) Web verification memory")
            for idx, item in enumerate(research[:5], 1):
                lines.append(
                    f"- R{idx}: query={item.get('query', '')} | status={item.get('status', '')} | "
                    f"rank={item.get('ranking_score', 0)} | evidence_count={item.get('evidence_count', 0)} | "
                    f"tiers={','.join(item.get('source_tiers', [])[:3]) or '-'}"
                )
                summary = str(item.get("summary", "")).strip()
                if summary:
                    lines.append(f"  summary={summary[:600]}")

        web_queries = pack.get("recommended_web_queries", []) or []
        if web_queries:
            lines.append("3) Recommended web refresh queries")
            for query in web_queries[:4]:
                lines.append(f"- {query}")
        return "\n".join(lines)

    def render_rag_contexts(self, pack: dict[str, Any], limit: int = 5) -> list[str]:
        contexts = []
        for event in (pack.get("events", []) or [])[:limit]:
            contexts.append(
                f"[뉴스 메모리 {event.get('date', '')}] {event.get('title', '')}\n"
                f"- 요약: {event.get('summary', '')}\n"
                f"- 상태: {event.get('memory_status', '')}, 품질점수={event.get('ranking_score', 0)}, "
                f"source_mix={event.get('source_mix', {})}"
            )
        for item in (pack.get("research", []) or [])[: max(0, limit - len(contexts))]:
            contexts.append(
                f"[웹검증 메모리 {item.get('created_at', '')}] query={item.get('query', '')}\n"
                f"- 요약: {str(item.get('summary', '')).strip()[:600]}\n"
                f"- evidence_count={item.get('evidence_count', 0)}, source_tiers={item.get('source_tiers', [])}"
            )
        return contexts

    def audit_rendered_contexts(
        self,
        *,
        pack: dict[str, Any],
        consumer: str,
        rendered_contexts: list[str],
        limit: int,
        truncated_chars: int | None = None,
    ) -> None:
        if not pack:
            return
        self.db.save_context_selection_audit(
            {
                "context_id": pack.get("query_hash"),
                "query": pack.get("query", ""),
                "consumer": consumer,
                "selected_json": {
                    "rendered_context_count": len(rendered_contexts),
                    "event_keys": [e.get("event_key") for e in (pack.get("events", []) or [])[:limit]],
                    "research_queries": [r.get("query") for r in (pack.get("research", []) or [])[:limit]],
                    "selection_reasons": ["recent", "term_match", "source_quality", "news_pack_quality"],
                },
                "excluded_json": {
                    "events_not_rendered": max(0, len(pack.get("events", []) or []) - limit),
                    "research_not_rendered": max(0, len(pack.get("research", []) or []) - limit),
                },
                "quality_json": pack.get("quality", {}) or {},
                "budget_json": {
                    "limit": limit,
                    "truncated_chars": truncated_chars,
                    "rendered_chars": sum(len(str(c)) for c in rendered_contexts),
                },
            }
        )

    def query_hash(
        self,
        query: str,
        *,
        tickers: list[str] | None = None,
        extra_terms: list[str] | None = None,
    ) -> str:
        normalized = " ".join(
            [
                re.sub(r"\s+", " ", str(query or "").strip().lower()),
                ",".join(sorted(str(t).strip().upper() for t in (tickers or []) if str(t).strip())),
                ",".join(sorted(str(t).strip().lower() for t in (extra_terms or []) if str(t).strip())),
            ]
        )
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def _decorate_event(self, event: dict[str, Any], articles: list[dict[str, Any]], terms: list[str]) -> dict[str, Any]:
        item = dict(event or {})
        decorated_articles = [self._decorate_article(article) for article in articles]
        source_mix: dict[str, int] = {}
        tier_mix: dict[str, int] = {}
        for article in decorated_articles:
            source = str(article.get("source", "") or "unknown")
            tier = str(article.get("source_tier", "") or "unknown")
            source_mix[source] = source_mix.get(source, 0) + 1
            tier_mix[tier] = tier_mix.get(tier, 0) + 1

        body = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        matched = [term for term in terms if str(term).lower() in body][:8]
        confidence = float(item.get("confidence", 0.0) or 0.0)
        source_count = int(item.get("source_count", 0) or 0)
        article_count = int(item.get("article_count", 0) or 0)
        official_bonus = 10 if any(t in tier_mix for t in ("regulatory", "company_ir")) else 0
        tier1_bonus = 4 * tier_mix.get("tier1_media", 0)
        matched_bonus = 5 * len(matched)
        recency_bonus = self._recency_bonus(item.get("updated_at") or item.get("date"))
        ranking_score = round(
            (confidence * 45.0)
            + (source_count * 4.0)
            + (article_count * 1.5)
            + official_bonus
            + tier1_bonus
            + matched_bonus
            + recency_bonus,
            2,
        )
        item["articles"] = decorated_articles
        item["source_mix"] = source_mix
        item["tier_mix"] = tier_mix
        item["matched_terms"] = matched
        item["ranking_score"] = ranking_score
        item["memory_status"] = self._event_memory_status(item, tier_mix)
        return item

    def _decorate_article(self, article: dict[str, Any]) -> dict[str, Any]:
        item = dict(article or {})
        source = str(item.get("source", "")).strip()
        item["source_tier"] = self._source_tier(source, item.get("canonical_url") or item.get("url"))
        return item

    def _decorate_research(self, research: dict[str, Any], terms: list[str]) -> dict[str, Any]:
        item = dict(research or {})
        evidence_count = int(item.get("evidence_count", 0) or 0)
        source_tiers = item.get("source_tiers", []) or []
        quality_avg = float(item.get("source_quality_avg", 0.0) or 0.0)
        term_body = f"{item.get('topic', '')} {item.get('query', '')}".lower()
        term_hits = sum(1 for t in terms if str(t).lower() in term_body)
        official_bonus = 8 if any(t in source_tiers for t in ("regulatory", "company_ir")) else 0
        ranking_score = round(
            evidence_count * 7.0
            + quality_avg * 8.0
            + len(source_tiers) * 2.0
            + term_hits * 4.0
            + official_bonus
            + self._recency_bonus(item.get("created_at")),
            2,
        )
        item["ranking_score"] = ranking_score
        return item

    def _assess_pack_quality(self, events: list[dict[str, Any]], research: list[dict[str, Any]]) -> dict[str, Any]:
        limitations = []
        if not events:
            limitations.append("no_local_event_memory")
        if not research:
            limitations.append("no_web_verification_memory")
        official_events = sum(
            1 for event in events
            if any(t in (event.get("tier_mix") or {}) for t in ("regulatory", "company_ir"))
        )
        high_quality_research = sum(
            1 for item in research
            if any(t in (item.get("source_tiers") or []) for t in ("regulatory", "company_ir", "tier1_media"))
        )
        if events and official_events == 0:
            limitations.append("no_official_source_in_local_events")
        if research and high_quality_research == 0:
            limitations.append("weak_research_source_tiers")
        if len(events) < 2:
            limitations.append("thin_local_coverage")

        avg_event_score = sum(float(e.get("ranking_score", 0.0) or 0.0) for e in events[:5]) / max(1, min(len(events), 5))
        avg_research_score = sum(float(r.get("ranking_score", 0.0) or 0.0) for r in research[:5]) / max(1, min(len(research), 5))
        score = round(min(100.0, (avg_event_score * 0.55) + (avg_research_score * 0.45)), 2)
        web_required = bool(
            "no_web_verification_memory" in limitations
            or "no_official_source_in_local_events" in limitations
            or "weak_research_source_tiers" in limitations
        )
        if score >= 70 and not web_required:
            state = "strong"
        elif score >= 45:
            state = "usable_needs_refresh" if web_required else "usable"
        elif events or research:
            state = "weak"
        else:
            state = "empty"
        return {
            "score": score,
            "state": state,
            "limitations": limitations,
            "web_required": web_required,
            "event_count": len(events),
            "research_count": len(research),
            "official_event_count": official_events,
            "high_quality_research_count": high_quality_research,
        }

    def _recommend_web_queries(
        self,
        query: str,
        tickers: list[str],
        events: list[dict[str, Any]],
        quality: dict[str, Any],
    ) -> list[str]:
        if not quality.get("web_required"):
            return []
        queries = []
        for ticker in tickers[:3]:
            t = str(ticker).strip().upper()
            if t:
                queries.append(f"{t} investor relations latest filing guidance")
                queries.append(f"{t} Reuters latest earnings outlook risk")
        for event in events[:2]:
            title = str(event.get("title", "")).strip()
            if title:
                queries.append(f"{title} official source filing press release")
        if query:
            queries.append(str(query).strip()[:180])
        out = []
        seen = set()
        for q in queries:
            qn = re.sub(r"\s+", " ", q.strip().lower())
            if qn and qn not in seen:
                out.append(q.strip())
                seen.add(qn)
        return out[:5]

    def _save_context_audit(
        self,
        *,
        pack: dict[str, Any],
        consumer: str,
        selected_events: list[dict[str, Any]],
        selected_research: list[dict[str, Any]],
        event_limit: int,
        evidence_limit: int,
        lookback_hours: int,
    ) -> None:
        try:
            self.db.save_context_selection_audit(
                {
                    "context_id": pack.get("query_hash"),
                    "query": pack.get("query", ""),
                    "consumer": consumer,
                    "selected_json": {
                        "event_keys": [e.get("event_key") for e in selected_events],
                        "event_scores": [
                            {"event_key": e.get("event_key"), "ranking_score": e.get("ranking_score"), "memory_status": e.get("memory_status")}
                            for e in selected_events[:10]
                        ],
                        "research_queries": [r.get("query") for r in selected_research],
                        "research_scores": [
                            {"query": r.get("query"), "ranking_score": r.get("ranking_score"), "source_tiers": r.get("source_tiers", [])}
                            for r in selected_research[:10]
                        ],
                        "selection_reasons": ["lookback_window", "query_term_match", "source_tier", "recency"],
                    },
                    "excluded_json": {
                        "not_loaded_reason": "limited_by_event_and_evidence_caps",
                    },
                    "quality_json": pack.get("quality", {}) or {},
                    "budget_json": {
                        "event_limit": event_limit,
                        "evidence_limit": evidence_limit,
                        "lookback_hours": lookback_hours,
                        "event_count": len(selected_events),
                        "research_count": len(selected_research),
                    },
                }
            )
        except Exception:
            pass

    def _event_memory_status(self, event: dict[str, Any], tier_mix: dict[str, int]) -> str:
        if tier_mix.get("regulatory") or tier_mix.get("company_ir"):
            return "officially_supported"
        if int(event.get("source_count", 0) or 0) >= 2 and tier_mix.get("tier1_media", 0) >= 1:
            return "multi_source_supported"
        if int(event.get("source_count", 0) or 0) <= 1:
            return "single_source_watch"
        return "local_unverified"

    def _source_tier(self, source: str, url: str | None = None) -> str:
        src = str(source or "").lower()
        url_text = str(url or "").lower()
        if "sec" in src or "sec.gov" in url_text or "fed" in src or "federalreserve.gov" in url_text:
            return "regulatory"
        if "investor." in url_text or "/investor" in url_text or "ir." in url_text:
            return "company_ir"
        if "reuters" in src or "nytimes" in src or "nytimes.com" in url_text or "reuters.com" in url_text:
            return "tier1_media"
        if src:
            return "secondary"
        return "unknown"

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
        return max(0.0, 12.0 - min(hours, 72.0) / 6.0)
