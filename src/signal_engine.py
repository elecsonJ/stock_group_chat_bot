import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any

from db_manager import DBManager
from ontology import HybridResearchPlanner, OntologyStore


class SignalEngine:
    """
    뉴스 이벤트를 단기 트레이딩용 시그널로 점수화하고 승인 요청을 생성합니다.
    """

    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()
        self.verify_budget_default = max(1, int(os.getenv("SIGNAL_VERIFY_BUDGET", "3")))
        self.recency_hours_default = max(6, int(os.getenv("SIGNAL_RECENCY_HOURS", "36")))
        self.debate_auto_min_score = max(50.0, float(os.getenv("DEBATE_AUTO_MIN_SCORE", "75")))
        self.debate_cooldown_minutes = max(5, int(os.getenv("DEBATE_AUTO_COOLDOWN_MIN", "30")))
        self.debate_frontier_mode = os.getenv("DEBATE_FRONTIER_MODE", "gated").strip().lower()
        self.debate_review_min_score = max(50.0, float(os.getenv("DEBATE_REVIEW_MIN_SCORE", "70")))
        self.debate_require_verified = os.getenv("DEBATE_REQUIRE_VERIFIED", "true").strip().lower() not in {"0", "false", "no"}
        self.review_trigger_min_score = max(40.0, float(os.getenv("REVIEW_TRIGGER_MIN_SCORE", "70")))
        self.high_quality_source_tiers = {"regulatory", "company_ir", "tier1_media"}
        try:
            self.ontology = OntologyStore(db_path=self.db.db_path)
            self.planner = HybridResearchPlanner(self.ontology)
        except Exception:
            self.ontology = None
            self.planner = None
        self.company_alias_ticker = {
            "nvidia": "NVDA",
            "tesla": "TSLA",
            "apple": "AAPL",
            "microsoft": "MSFT",
            "amazon": "AMZN",
            "meta": "META",
            "google": "GOOGL",
            "alphabet": "GOOGL",
            "amd": "AMD",
            "broadcom": "AVGO",
            "tsmc": "TSM",
            "samsung": "005930.KS",
            "sk hynix": "000660.KS",
            "삼성전자": "005930.KS",
            "하이닉스": "000660.KS",
        }
        self.impact_keywords = {
            "guidance": 8,
            "outlook": 5,
            "downgrade": 7,
            "upgrade": 6,
            "lawsuit": 8,
            "investigation": 8,
            "ban": 8,
            "tariff": 6,
            "regulation": 6,
            "acquisition": 6,
            "merger": 6,
            "recall": 9,
            "bankruptcy": 10,
            "earnings": 5,
            "forecast": 5,
            "supply": 4,
            "shortage": 6,
            "수주": 7,
            "규제": 7,
            "소송": 8,
            "리콜": 9,
            "가이던스": 7,
            "인수": 6,
            "합병": 6,
            "실적": 5,
            "관세": 6,
            "공급": 4,
        }
        self.bullish_keywords = (
            "beat", "raised", "upgrade", "approval", "contract", "award", "record",
            "수주", "상향", "승인", "호재", "증설", "계약",
        )
        self.bearish_keywords = (
            "miss", "cut", "downgrade", "lawsuit", "investigation", "recall", "fraud",
            "bankruptcy", "probe", "ban", "경고", "하향", "소송", "리콜", "규제", "악재",
        )
        self.immediate_keywords = (
            "bankruptcy", "fraud", "probe", "investigation", "recall", "ban", "ceo resign",
            "파산", "회계", "수사", "리콜", "금지", "사임",
        )

    def _build_ontology_plan(self, event: dict[str, Any]) -> dict[str, Any]:
        if self.planner is None:
            return {}
        query = re.sub(
            r"\s+",
            " ",
            f"{str(event.get('title', '')).strip()} {str(event.get('summary', '')).strip()}",
        ).strip()
        if not query:
            return {}
        try:
            return self.planner.build_plan(query)
        except Exception:
            return {}

    def _verification_quality(self, verification: dict[str, Any] | None) -> dict[str, Any]:
        payload = verification or {}
        source_tiers = {
            str(t).strip()
            for t in (payload.get("source_tiers") or [])
            if str(t).strip()
        }
        verdict = str(payload.get("verdict", "")).strip().lower()
        return {
            "verdict": verdict,
            "source_tiers": sorted(source_tiers),
            "high_quality": bool(source_tiers & self.high_quality_source_tiers),
            "verified": verdict == "verified",
        }

    def _compute_debate_priority(
        self,
        score_total: float,
        urgency: str,
        portfolio_hit: bool,
        verification_quality: dict[str, Any],
        strong_hidden: bool,
    ) -> int:
        priority = int(min(100, max(10, round(score_total))))
        if urgency == "immediate":
            priority += 18
        elif urgency == "same_day":
            priority += 8
        if portfolio_hit:
            priority += 12
        if verification_quality.get("high_quality"):
            priority += 8
        if strong_hidden:
            priority += 6
        return min(100, priority)

    def _build_debate_reason(
        self,
        score_total: float,
        urgency: str,
        portfolio_hit: bool,
        verification_quality: dict[str, Any],
        hidden_candidates: list[dict[str, Any]],
    ) -> str:
        reasons = []
        if urgency == "immediate":
            reasons.append("immediate_urgency")
        if score_total >= self.debate_auto_min_score:
            reasons.append("high_score")
        if portfolio_hit:
            reasons.append("portfolio_hit")
        if verification_quality.get("verified"):
            reasons.append("verified")
        if verification_quality.get("high_quality"):
            reasons.append("high_quality_source")
        if any(float(c.get("validation_score", 0.0) or 0.0) >= 0.65 for c in hidden_candidates):
            reasons.append("strong_hidden_candidate")
        return ",".join(reasons) or "manual_policy"

    def _build_debate_topic(self, event: dict[str, Any], hidden_candidates: list[dict[str, Any]]) -> str:
        title = str(event.get("title", "")).strip()
        summary = str(event.get("summary", "")).strip()
        topic = f"{title}\n\n{summary}".strip()
        hidden_preview = []
        for candidate in hidden_candidates[:3]:
            ticker = str(candidate.get("ticker") or "").strip()
            name = str(candidate.get("name") or candidate.get("canonical_name") or "").strip()
            label = " ".join(part for part in [ticker, name] if part).strip()
            if label:
                hidden_preview.append(label)
        if hidden_preview:
            topic += f"\n\n숨은 연결 후보: {', '.join(hidden_preview)}"
        return topic.strip()

    def _debate_cost_gate(
        self,
        *,
        score_total: float,
        urgency: str,
        portfolio_hit: bool,
        verification_quality: dict[str, Any],
        strong_hidden: bool,
    ) -> dict[str, Any]:
        mode = self.debate_frontier_mode if self.debate_frontier_mode in {"off", "manual", "gated", "auto"} else "gated"
        verified = bool(verification_quality.get("verified"))
        high_quality = bool(verification_quality.get("high_quality"))
        reasons = []
        if mode == "off":
            return {"queue": False, "status": "blocked", "queue_status": "blocked", "reasons": ["frontier_mode_off"]}

        if urgency == "immediate":
            reasons.append("immediate")
        if score_total >= self.debate_auto_min_score:
            reasons.append("score_auto")
        if score_total >= self.debate_review_min_score:
            reasons.append("score_review")
        if portfolio_hit:
            reasons.append("portfolio_hit")
        if verified:
            reasons.append("verified")
        if high_quality:
            reasons.append("high_quality")
        if strong_hidden:
            reasons.append("strong_hidden")

        if mode == "manual":
            return {"queue": True, "status": "review_required", "queue_status": "pending", "reasons": reasons or ["manual_mode"]}
        if mode == "auto":
            return {"queue": True, "status": "auto_approved", "queue_status": "pending", "reasons": reasons or ["auto_mode"]}

        auto_ok = (
            score_total >= self.debate_auto_min_score
            and (portfolio_hit or high_quality or urgency == "immediate")
            and ((not self.debate_require_verified) or verified)
        )
        if auto_ok:
            return {"queue": True, "status": "auto_approved", "queue_status": "pending", "reasons": reasons}

        review_ok = (
            score_total >= self.debate_review_min_score
            or portfolio_hit
            or high_quality
            or strong_hidden
        )
        if review_ok:
            return {"queue": True, "status": "review_required", "queue_status": "pending", "reasons": reasons}
        return {"queue": False, "status": "blocked", "queue_status": "blocked", "reasons": reasons or ["below_review_gate"]}

    def _maybe_enqueue_debate(
        self,
        event: dict[str, Any],
        event_id: str,
        eval_data: dict[str, Any],
        verification: dict[str, Any],
        ontology_plan: dict[str, Any],
    ) -> dict[str, object]:
        score_total = float(eval_data.get("score_total", 0.0) or 0.0)
        urgency = str(eval_data.get("urgency", "")).strip().lower()
        portfolio_hit = bool((eval_data.get("score_json") or {}).get("portfolio_hit", False))
        verification_quality = self._verification_quality(verification)
        hidden_candidates = list((ontology_plan or {}).get("hidden_candidates", []) or [])
        strong_hidden = any(float(c.get("validation_score", 0.0) or 0.0) >= 0.65 for c in hidden_candidates)
        candidate = any(
            [
                urgency == "immediate",
                score_total >= self.debate_auto_min_score,
                portfolio_hit,
                verification_quality.get("high_quality"),
                strong_hidden,
            ]
        )
        if not candidate:
            return {"created": False, "merged": False, "queue_id": None, "reason": "policy_not_met"}

        cost_gate = self._debate_cost_gate(
            score_total=score_total,
            urgency=urgency,
            portfolio_hit=portfolio_hit,
            verification_quality=verification_quality,
            strong_hidden=strong_hidden,
        )
        if not cost_gate.get("queue"):
            return {
                "created": False,
                "merged": False,
                "queue_id": None,
                "reason": "cost_gate_blocked",
                "cost_gate": cost_gate,
            }

        tickers = list(eval_data.get("related_tickers", []) or [])
        if not tickers:
            tickers = [
                str(c.get("ticker") or "").strip()
                for c in hidden_candidates
                if str(c.get("ticker") or "").strip()
            ]
        row = {
            "event_id": event_id,
            "event_key": event.get("event_key"),
            "ticker": tickers[0] if tickers else "",
            "direction": eval_data.get("direction", "neutral"),
            "urgency": urgency,
            "priority": self._compute_debate_priority(
                score_total=score_total,
                urgency=urgency,
                portfolio_hit=portfolio_hit,
                verification_quality=verification_quality,
                strong_hidden=strong_hidden,
            ),
            "topic": self._build_debate_topic(event, hidden_candidates),
            "reason": self._build_debate_reason(
                score_total=score_total,
                urgency=urgency,
                portfolio_hit=portfolio_hit,
                verification_quality=verification_quality,
                hidden_candidates=hidden_candidates,
            ),
            "status": cost_gate.get("queue_status", "pending"),
            "cost_gate_status": cost_gate.get("status", ""),
            "cost_gate_json": cost_gate,
            "trigger_json": {
                "event_id": event_id,
                "score_total": score_total,
                "direction": eval_data.get("direction"),
                "urgency": urgency,
                "portfolio_hit": portfolio_hit,
                "verification_verdict": verification_quality.get("verdict", ""),
                "source_tiers": verification_quality.get("source_tiers", []),
                "hidden_candidates": hidden_candidates[:3],
                "web_queries": (ontology_plan or {}).get("web_queries", [])[:3],
                "cost_gate": cost_gate,
            },
        }
        return self.db.enqueue_debate_candidate(
            row,
            cooldown_minutes=self.debate_cooldown_minutes,
        )

    def _emit_review_triggers(
        self,
        event: dict[str, Any],
        event_id: str,
        eval_data: dict[str, Any],
        verification: dict[str, Any],
        ontology_plan: dict[str, Any],
        portfolio_tickers: list[str],
    ) -> list[dict[str, object]]:
        portfolio_set = {str(t or "").strip().upper() for t in portfolio_tickers if str(t or "").strip()}
        if not portfolio_set:
            return []

        score_total = float(eval_data.get("score_total", 0.0) or 0.0)
        direction = str(eval_data.get("direction", "neutral")).strip().lower()
        urgency = str(eval_data.get("urgency", "monitor")).strip().lower()
        verification_quality = self._verification_quality(verification)
        verified_or_strong = bool(
            verification_quality.get("verified")
            or score_total >= max(self.review_trigger_min_score + 10.0, 85.0)
        )
        if direction not in {"bullish", "bearish"} or not verified_or_strong:
            return []

        direct_hits = [
            str(t).strip().upper()
            for t in (eval_data.get("related_tickers") or [])
            if str(t).strip().upper() in portfolio_set
        ]
        hidden_hits = [
            str(c.get("ticker") or "").strip().upper()
            for c in ((ontology_plan or {}).get("hidden_candidates") or [])
            if str(c.get("ticker") or "").strip().upper() in portfolio_set
            and float(c.get("validation_score", 0.0) or 0.0) >= 0.65
        ]

        created = []
        source_tiers = verification_quality.get("source_tiers", [])
        title = str(event.get("title", "")).strip()
        base_detail = {
            "event_id": event_id,
            "title": title,
            "score_total": score_total,
            "direction": direction,
            "urgency": urgency,
            "source_tiers": source_tiers,
            "verification_verdict": verification_quality.get("verdict", ""),
            "web_queries": (ontology_plan or {}).get("web_queries", [])[:3],
        }

        for ticker in direct_hits:
            trigger_type = "add_review"
            if direction == "bearish":
                trigger_type = "exit_review" if urgency == "immediate" or score_total >= 85 else "reduce_review"
            result = self.db.upsert_investment_review_trigger(
                {
                    "event_id": event_id,
                    "ticker": ticker,
                    "trigger_type": trigger_type,
                    "priority": self._compute_debate_priority(
                        score_total=score_total,
                        urgency=urgency,
                        portfolio_hit=True,
                        verification_quality=verification_quality,
                        strong_hidden=False,
                    ),
                    "summary": f"{ticker} {trigger_type}: {title}",
                    "detail_json": {**base_detail, "origin": "direct"},
                }
            )
            created.append({"ticker": ticker, "trigger_type": trigger_type, **result})

        for ticker in hidden_hits:
            if ticker in direct_hits:
                continue
            trigger_type = "add_review" if direction == "bullish" else "hedge_review"
            result = self.db.upsert_investment_review_trigger(
                {
                    "event_id": event_id,
                    "ticker": ticker,
                    "trigger_type": trigger_type,
                    "priority": self._compute_debate_priority(
                        score_total=score_total,
                        urgency=urgency,
                        portfolio_hit=False,
                        verification_quality=verification_quality,
                        strong_hidden=True,
                    ),
                    "summary": f"{ticker} {trigger_type}: {title}",
                    "detail_json": {**base_detail, "origin": "hidden_candidate"},
                }
            )
            created.append({"ticker": ticker, "trigger_type": trigger_type, **result})

        return created

    def _build_event_id(self, event: dict[str, Any]) -> str:
        # title 변형에 흔들리지 않도록 event_key 중심으로 고정
        seed = f"{event.get('event_key','')}|{event.get('date','')}"
        h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10].upper()
        return f"SG{h}"

    def _extract_related_tickers(self, text: str, portfolio_tickers: list[str]) -> tuple[list[str], bool]:
        tickers = []
        seen = set()
        common_acronyms = {
            "AI", "CEO", "CFO", "COO", "CPI", "PPI", "GDP", "VIX", "PER", "EPS",
            "IPO", "FDA", "SEC", "FED", "ETF", "USD", "KRW", "NYSE", "NASDAQ",
        }
        portfolio_set = {str(t or "").upper().strip() for t in portfolio_tickers if str(t or "").strip()}
        for t in re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", text):
            if t in common_acronyms and t not in portfolio_set:
                continue
            if t not in seen:
                tickers.append(t)
                seen.add(t)

        lower_text = text.lower()
        for alias, mapped_ticker in self.company_alias_ticker.items():
            if alias in lower_text and mapped_ticker not in seen:
                tickers.append(mapped_ticker)
                seen.add(mapped_ticker)

        p_hit = False
        low = lower_text
        for pt in portfolio_tickers:
            p = (pt or "").upper().strip()
            if not p:
                continue
            if p in text.upper():
                p_hit = True
                if p not in seen:
                    tickers.append(p)
                    seen.add(p)
            # KS/KQ 종목은 숫자 부분도 느슨하게 확인
            base = p.split(".")[0]
            if base and base in low:
                p_hit = True
                if p not in seen:
                    tickers.append(p)
                    seen.add(p)
        return tickers[:3], p_hit

    def _score_event(self, event: dict[str, Any], portfolio_tickers: list[str]) -> dict[str, Any]:
        title = str(event.get("title", ""))
        summary = str(event.get("summary", ""))
        body = f"{title}\n{summary}"
        lower_body = body.lower()

        confidence = float(event.get("confidence", 0.0) or 0.0)
        source_count = int(event.get("source_count", 0) or 0)
        article_count = int(event.get("article_count", 0) or 0)

        related_tickers, portfolio_hit = self._extract_related_tickers(body, portfolio_tickers)

        impact_score = 0
        matched_impacts = []
        for kw, pts in self.impact_keywords.items():
            if kw in lower_body:
                impact_score += pts
                matched_impacts.append(kw)

        bull_hits = [k for k in self.bullish_keywords if k in lower_body]
        bear_hits = [k for k in self.bearish_keywords if k in lower_body]
        immediate_hits = [k for k in self.immediate_keywords if k in lower_body]

        base_score = (confidence * 45.0) + (min(source_count, 8) * 4.0) + (min(article_count, 12) * 2.0)
        score_total = base_score + impact_score + (25.0 if portfolio_hit else 0.0)
        score_total = max(0.0, min(100.0, round(score_total, 2)))

        direction = "neutral"
        if len(bear_hits) > len(bull_hits) and bear_hits:
            direction = "bearish"
        elif len(bull_hits) > len(bear_hits) and bull_hits:
            direction = "bullish"

        if immediate_hits or score_total >= 82:
            urgency = "immediate"
        elif score_total >= 65:
            urgency = "same_day"
        else:
            urgency = "monitor"

        breakdown = {
            "base_score": round(base_score, 2),
            "impact_score": impact_score,
            "portfolio_hit": portfolio_hit,
            "source_count": source_count,
            "article_count": article_count,
            "confidence": confidence,
            "impact_keywords": matched_impacts[:8],
            "bull_hits": bull_hits[:5],
            "bear_hits": bear_hits[:5],
            "immediate_hits": immediate_hits[:5],
        }

        return {
            "score_total": score_total,
            "direction": direction,
            "urgency": urgency,
            "related_tickers": related_tickers,
            "score_json": breakdown,
        }

    def _parse_iso(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            s = str(value).replace("Z", "+00:00")
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def _is_recent_event(self, event: dict[str, Any], recency_hours: int) -> bool:
        now = datetime.now()
        candidates = [
            self._parse_iso(event.get("updated_at")),
            self._parse_iso(event.get("date")),
        ]
        for dt in candidates:
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return (now - dt) <= timedelta(hours=max(1, recency_hours))
        return True

    async def _web_verify_event(
        self,
        checker: Any,
        event: dict[str, Any],
        event_eval: dict[str, Any],
    ) -> dict[str, Any]:
        title = str(event.get("title", "")).strip()
        summary = str(event.get("summary", "")).strip()
        related = event_eval.get("related_tickers", []) or []
        query_prefix = f"{related[0]} " if related else ""
        query = re.sub(r"\s+", " ", f"{query_prefix}{title} {summary[:80]}").strip()[:220]
        if not query:
            return {"verdict": "insufficient", "reason": "empty_query"}

        try:
            package = await checker.run_deep_research_package(query)
        except Exception as exc:
            return {
                "query": query,
                "verdict": "insufficient",
                "direction": event_eval.get("direction", "neutral"),
                "confidence": 0.0,
                "evidence_count": 0,
                "domains": [],
                "source_tiers": [],
                "bull_hits": [],
                "bear_hits": [],
                "summary": "",
                "limitations": [f"web_verification_error: {str(exc)[:160]}"],
                "score_bonus": -8.0,
                "evidence_ids": [],
                "verified_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            }
        try:
            self.db.save_research_evidence(title[:100], query, package)
        except Exception:
            pass

        evidences = package.get("evidences", []) if isinstance(package, dict) else []
        domains = list({str(e.get("domain", "")).strip() for e in evidences if e.get("domain")})
        source_tiers = list({str(e.get("source_tier", "")).strip() for e in evidences if e.get("source_tier")})
        body = " ".join(
            [
                str(e.get("snippet", "")) + " " + str(e.get("excerpt", ""))
                for e in evidences
                if isinstance(e, dict)
            ]
        ).lower()
        bull_hits = [k for k in self.bullish_keywords if k in body]
        bear_hits = [k for k in self.bearish_keywords if k in body]

        direction = event_eval.get("direction", "neutral")
        if len(bear_hits) >= len(bull_hits) + 2:
            direction = "bearish"
        elif len(bull_hits) >= len(bear_hits) + 2:
            direction = "bullish"

        evidence_count = len(evidences)
        domain_count = len(domains)
        verdict = "verified" if evidence_count >= 2 and domain_count >= 2 else "insufficient"
        confidence = min(0.95, 0.2 + 0.08 * domain_count + 0.05 * min(evidence_count, 8))
        score_bonus = min(18.0, float(domain_count * 2 + evidence_count * 1.5))
        if verdict != "verified":
            score_bonus = -8.0

        evidence_ids = []
        for e in evidences[:8]:
            eid = str(e.get("global_evidence_id") or e.get("evidence_id") or "").strip()
            if eid:
                evidence_ids.append(eid)

        return {
            "query": query,
            "verdict": verdict,
            "direction": direction,
            "confidence": round(confidence, 3),
            "evidence_count": evidence_count,
            "domains": domains[:8],
            "source_tiers": source_tiers[:5],
            "bull_hits": bull_hits[:6],
            "bear_hits": bear_hits[:6],
            "summary": package.get("summary", "") if isinstance(package, dict) else "",
            "limitations": package.get("limitations", []) if isinstance(package, dict) else [],
            "score_bonus": round(score_bonus, 2),
            "evidence_ids": evidence_ids,
            "verified_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        }

    def _build_recommendations(self, event_id: str, event_eval: dict[str, Any]) -> list[dict]:
        direction = event_eval.get("direction", "neutral")
        urgency = event_eval.get("urgency", "monitor")
        score_total = float(event_eval.get("score_total", 0.0))
        tickers = event_eval.get("related_tickers", []) or []
        if direction == "neutral" or not tickers:
            return []

        side = "BUY" if direction == "bullish" else "SELL"
        ttl_sec = 900 if urgency == "immediate" else (3600 if urgency == "same_day" else 7200)
        conf = min(0.95, 0.35 + (score_total / 120.0))
        recs = []
        for t in tickers[:2]:
            recs.append(
                {
                    "event_id": event_id,
                    "ticker": t,
                    "side": side,
                    "size_rule": "기본 1단위(포트폴리오 2% 리스크 한도)",
                    "entry_rule": "즉시 시가 근접 체결" if urgency == "immediate" else "15분 내 추세 확인 후 진입",
                    "stop_rule": "손절 -1.5% 또는 이벤트 반대 공시 발생 시 청산",
                    "ttl_sec": ttl_sec,
                    "confidence": round(conf, 3),
                    "rationale": f"{direction}/{urgency} 이벤트 스코어 {score_total}",
                    "status": "pending_approval",
                }
            )
        return recs

    async def generate_signals_from_news(
        self,
        portfolio_tickers: list[str],
        max_events: int = 20,
        threshold: float = 58.0,
        checker: Any | None = None,
        verify_new_only: bool = True,
        verify_budget: int | None = None,
        recency_hours: int | None = None,
    ) -> list[dict]:
        self.db.mark_expired_approvals()
        verify_budget_val = max(1, int(verify_budget or self.verify_budget_default))
        recency_hours_val = max(1, int(recency_hours or self.recency_hours_default))
        events = self.db.get_latest_news_events(limit=max_events)
        out = []
        verify_used = 0
        for e in events:
            if not self._is_recent_event(e, recency_hours=recency_hours_val):
                continue
            eval_data = self._score_event(e, portfolio_tickers=portfolio_tickers)
            score_total = float(eval_data.get("score_total", 0.0))
            portfolio_hit = bool((eval_data.get("score_json") or {}).get("portfolio_hit", False))
            if score_total < threshold and not portfolio_hit:
                continue

            event_id = self._build_event_id(e)
            existing = self.db.get_signal_event(event_id)
            existed = existing is not None
            existing_status = str((existing or {}).get("status", "")).strip().lower()
            is_terminal_event = existing_status in {"executed", "rejected"}

            verification = existing.get("verification_json", {}) if existing else {}
            should_verify = (
                checker is not None
                and verify_used < verify_budget_val
                and ((not verify_new_only) or (not existed))
            )
            if should_verify:
                verification = await self._web_verify_event(checker, e, eval_data)
                verify_used += 1

            v_bonus = float((verification or {}).get("score_bonus", 0.0) or 0.0)
            score_total = max(0.0, min(100.0, round(score_total + v_bonus, 2)))
            eval_data["score_total"] = score_total
            v_dir = str((verification or {}).get("direction", "")).strip().lower()
            if v_dir in {"bullish", "bearish"}:
                eval_data["direction"] = v_dir
            ontology_plan = self._build_ontology_plan(e)

            recs = []
            if not is_terminal_event:
                if (verification or {}).get("verdict", "verified") == "verified":
                    recs = self._build_recommendations(event_id, {**eval_data, "score_total": score_total})
                elif not verification:
                    # 웹검증 미수행(예산/조건)인 경우 기존 동작 유지
                    recs = self._build_recommendations(event_id, {**eval_data, "score_total": score_total})

            status = existing_status if is_terminal_event else ("pending_approval" if recs else "monitor_only")

            signal_row = {
                "event_id": event_id,
                "event_key": e.get("event_key"),
                "date": e.get("date"),
                "detected_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                "title": e.get("title"),
                "summary": e.get("summary"),
                "score_total": score_total,
                "score_json": eval_data.get("score_json", {}),
                "related_tickers": eval_data.get("related_tickers", []),
                "direction": eval_data.get("direction"),
                "urgency": eval_data.get("urgency"),
                "confidence": float(e.get("confidence", 0.0) or 0.0),
                "status": status,
                "evidence_ids": (verification or {}).get("evidence_ids", []),
                "verification_json": verification or {},
                "last_verified_at": (verification or {}).get("verified_at"),
            }
            self.db.upsert_signal_event(signal_row)

            if recs and not is_terminal_event:
                self.db.replace_recommendations(event_id, recs)
                ttl = max(int(r.get("ttl_sec", 0) or 0) for r in recs)
                opened = self.db.upsert_approval_request(
                    event_id,
                    ttl_sec=max(300, ttl),
                    allow_reopen_terminal=False,
                )
                if not opened:
                    # 승인 상태가 터미널인 이벤트는 pending으로 되돌리지 않는다.
                    approval = self.db.get_approval_request(event_id) or {}
                    a_state = str(approval.get("state", "")).strip().lower()
                    if a_state in {"executed", "rejected"}:
                        status = a_state
                        self.db.set_signal_event_status(event_id, status)
            elif not is_terminal_event:
                approval = self.db.get_approval_request(event_id) or {}
                if str(approval.get("state", "")).strip().lower() in {"pending", "approved"}:
                    self.db.supersede_signal_workflow(
                        event_id,
                        note="verification no longer actionable",
                    )
                self.db.set_recommendations_status(event_id, "superseded")

            debate_queue_result = {"created": False, "merged": False, "queue_id": None, "reason": "terminal_event"}
            review_triggers = []
            if not is_terminal_event:
                debate_queue_result = self._maybe_enqueue_debate(
                    event=e,
                    event_id=event_id,
                    eval_data=eval_data,
                    verification=verification or {},
                    ontology_plan=ontology_plan,
                )
                review_triggers = self._emit_review_triggers(
                    event=e,
                    event_id=event_id,
                    eval_data=eval_data,
                    verification=verification or {},
                    ontology_plan=ontology_plan,
                    portfolio_tickers=portfolio_tickers,
                )
            out.append(
                {
                    "event_id": event_id,
                    "title": e.get("title", ""),
                    "score_total": score_total,
                    "direction": eval_data.get("direction"),
                    "urgency": eval_data.get("urgency"),
                    "status": status,
                    "new": not existed,
                    "related_tickers": eval_data.get("related_tickers", []),
                    "verified": (verification or {}).get("verdict") == "verified",
                    "verification_query": (verification or {}).get("query", ""),
                    "debate_queue": debate_queue_result,
                    "review_triggers": review_triggers,
                    "hidden_candidates": (ontology_plan or {}).get("hidden_candidates", [])[:3],
                }
            )
        return out

    def render_signal_list_text(self, limit: int = 12) -> str:
        rows = self.db.list_recent_signal_events(limit=limit)
        pending = self.db.list_pending_approvals(limit=limit)
        lines = ["🚨 **[단기 시그널 보드]**"]
        if not rows:
            lines.append("- 저장된 시그널이 없습니다. `!시그널`로 생성하세요.")
            return "\n".join(lines)
        for r in rows:
            lines.append(
                f"- `{r['event_id']}` | score={r['score_total']:.1f} | {r['direction']}/{r['urgency']} | "
                f"{r['status']} | tickers={','.join(r.get('related_tickers', [])[:2]) or '-'} | "
                f"verified_at={r.get('last_verified_at') or '-'}"
            )
            lines.append(f"  {r['title']}")
        if pending:
            lines.append("")
            lines.append("**[승인 대기]**")
            for p in pending[:8]:
                lines.append(f"- `{p['event_id']}` expires={p.get('expires_at') or '-'} | score={p['score_total']:.1f}")
        return "\n".join(lines)

    def render_signal_detail_text(self, event_id: str) -> str:
        event = self.db.get_signal_event(event_id)
        if not event:
            return f"⚠️ 이벤트를 찾지 못했습니다: `{event_id}`"
        recs = self.db.get_recommendations(event_id)
        approval = self.db.get_approval_request(event_id)
        lines = [
            f"📌 **[시그널 상세]** `{event_id}`",
            f"- title: {event.get('title','')}",
            f"- score: {event.get('score_total', 0):.1f}",
            f"- direction/urgency: {event.get('direction')}/{event.get('urgency')}",
            f"- status: {event.get('status')}",
            f"- related_tickers: {', '.join(event.get('related_tickers', [])) or '-'}",
            f"- summary: {event.get('summary','')}",
            f"- score_json: `{json.dumps(event.get('score_json', {}), ensure_ascii=False)[:900]}`",
        ]
        verification = event.get("verification_json", {}) or {}
        if verification:
            lines.append("**[웹검증 판정]**")
            lines.append(
                f"- verdict={verification.get('verdict')} | direction={verification.get('direction')} | "
                f"confidence={verification.get('confidence')} | evidence_count={verification.get('evidence_count')}"
            )
            lines.append(f"- query: {verification.get('query', '')}")
            if verification.get("limitations"):
                lines.append(f"- limitations: {verification.get('limitations')}")
        if recs:
            lines.append("**[주문 제안]**")
            for r in recs:
                lines.append(
                    f"- {r['ticker']} {r['side']} | conf={r['confidence']:.2f} | ttl={r['ttl_sec']}s | status={r['status']}"
                )
                lines.append(f"  entry={r['entry_rule']} | stop={r['stop_rule']}")
        if approval:
            lines.append("**[승인 상태]**")
            lines.append(
                f"- state={approval.get('state')} | requested_at={approval.get('requested_at')} | "
                f"expires_at={approval.get('expires_at')}"
            )
        queue_item = self.db.get_debate_queue_item(event_id)
        if queue_item:
            lines.append("**[자동 토론 큐]**")
            lines.append(
                f"- status={queue_item.get('status')} | priority={queue_item.get('priority')} | "
                f"reason={queue_item.get('reason')}"
            )
        review_rows = [
            row for row in self.db.list_investment_review_triggers(limit=10, statuses=["open"])
            if row.get("event_id") == event_id
        ]
        if review_rows:
            lines.append("**[투자 변화 트리거]**")
            for row in review_rows[:5]:
                lines.append(
                    f"- {row.get('ticker') or '-'} {row.get('trigger_type')} | "
                    f"priority={row.get('priority')} | status={row.get('status')}"
                )
        return "\n".join(lines)
