from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from db_manager import DBManager
from news_context_pack import NewsContextPackService


class DataQualityEvaluator:
    """
    데이터 수집/관리/판단 재료의 품질을 운영자가 한 번에 점검하기 위한 평가기.

    이 평가는 투자 판단을 내리지 않고, 시스템이 가진 데이터가 판단 가능한 상태인지
    freshness, coverage, source quality, verification, management 관점으로 점수화한다.
    """

    SCHEMA_VERSION = "data_quality.v1"

    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()
        self._pack_service = NewsContextPackService(self.db)

    def assess(self, lookback_hours: int = 168, limit: int = 1000) -> dict[str, Any]:
        lookback_hours = max(1, int(lookback_hours))
        now = datetime.now()
        since_dt = now - timedelta(hours=lookback_hours)
        since_date = since_dt.strftime("%Y-%m-%d")

        events = self.db.list_news_events_since(since_date, limit=limit)
        event_keys = [str(e.get("event_key") or "").strip() for e in events if e.get("event_key")]
        articles_by_event = self.db.list_news_articles_for_events(event_keys, limit_per_event=100)
        articles = [article for rows in articles_by_event.values() for article in rows]
        research = self.db.get_recent_research_context([], limit=limit, lookback_hours=lookback_hours)
        signals = self.db.list_signal_events_between(start_date=since_date, limit=limit)
        performance = self.db.list_signal_performance(limit=limit)
        packs = self._list_recent_packs(since_dt, limit=limit)
        checkpoints = self._list_checkpoints()
        event_audits = self.db.list_event_intake_audits(limit=limit)
        context_audits = self.db.list_context_selection_audits(limit=limit)
        market_reactions = self.db.list_market_reaction_snapshots(limit=limit)
        reconciliations = self.db.list_reconciliation_runs(limit=10)
        market_data_report = self._latest_market_data_report()

        latest_news_at = self._max_dt(
            [e.get("updated_at") or e.get("date") for e in events]
            + [a.get("fetched_at") or a.get("published_at") or a.get("date") for a in articles]
        )
        latest_research_at = self._max_dt([r.get("created_at") for r in research])
        latest_pack_at = self._max_dt([p.get("generated_at") for p in packs])

        event_count = len(events)
        article_count = len(articles)
        research_count = len(research)
        signal_count = len(signals)
        pack_count = len(packs)
        measured_event_ids = {str(p.get("event_id") or "") for p in performance if p.get("event_id")}
        signal_event_ids = {str(s.get("event_id") or "") for s in signals if s.get("event_id")}
        performance_coverage = (len(measured_event_ids & signal_event_ids) / len(signal_event_ids)) if signal_event_ids else 0.0

        event_without_articles = sum(1 for key in event_keys if not articles_by_event.get(key))
        orphan_event_ratio = (event_without_articles / event_count) if event_count else 0.0
        article_url_dupe_ratio = self._duplicate_ratio([a.get("canonical_url") or a.get("url") for a in articles])
        avg_articles_per_event = (article_count / event_count) if event_count else 0.0
        ingest_delays = [int(a.get("ingest_delay_sec", 0) or 0) for a in articles if int(a.get("ingest_delay_sec", 0) or 0) > 0]
        avg_ingest_delay_sec = round(sum(ingest_delays) / len(ingest_delays), 2) if ingest_delays else 0.0

        article_tiers = self._article_tier_counts(articles)
        high_quality_articles = article_tiers.get("regulatory", 0) + article_tiers.get("company_ir", 0) + article_tiers.get("tier1_media", 0)
        high_quality_article_ratio = (high_quality_articles / article_count) if article_count else 0.0

        verified_signals = [
            s for s in signals
            if (s.get("verification_json") or {}).get("verdict") == "verified"
        ]
        verification_coverage = (len(verified_signals) / signal_count) if signal_count else 0.0
        avg_evidence_per_signal = (
            sum(int((s.get("verification_json") or {}).get("evidence_count", 0) or 0) for s in signals) / signal_count
            if signal_count else 0.0
        )

        pack_states: dict[str, int] = {}
        pack_scores = []
        for pack in packs:
            quality = pack.get("quality", {}) or {}
            state = str(quality.get("state") or "unknown")
            pack_states[state] = pack_states.get(state, 0) + 1
            pack_scores.append(float(quality.get("score", 0.0) or 0.0))
        avg_pack_score = round(sum(pack_scores) / len(pack_scores), 2) if pack_scores else 0.0

        checkpoint_failures = [
            c for c in checkpoints
            if str(c.get("last_status") or "").lower() not in {"", "ok", "success", "completed"}
        ]

        scores = {
            "freshness": self._freshness_score(latest_news_at, now),
            "coverage": self._coverage_score(event_count, avg_articles_per_event, research_count, pack_count, lookback_hours),
            "source_quality": round(high_quality_article_ratio * 100.0, 2),
            "verification": round(min(100.0, verification_coverage * 70.0 + min(avg_evidence_per_signal, 4.0) / 4.0 * 30.0), 2),
            "management": self._management_score(orphan_event_ratio, article_url_dupe_ratio, checkpoint_failures),
            "performance_feedback": round(performance_coverage * 100.0, 2),
            "market_reaction": round(min(100.0, (len(market_reactions) / max(1, signal_count)) * 100.0), 2) if signal_count else 0.0,
            "reconciliation": 100.0 if reconciliations and reconciliations[0].get("status") == "ok" else 0.0,
            "market_data_connectivity": self._market_data_connectivity_score(market_data_report),
        }
        overall = round(
            scores["freshness"] * 0.19
            + scores["coverage"] * 0.19
            + scores["source_quality"] * 0.17
            + scores["verification"] * 0.17
            + scores["management"] * 0.13
            + scores["performance_feedback"] * 0.06
            + scores["market_reaction"] * 0.03
            + scores["reconciliation"] * 0.01
            + scores["market_data_connectivity"] * 0.05,
            2,
        )

        return {
            "schema_version": self.SCHEMA_VERSION,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "lookback_hours": lookback_hours,
            "overall_score": overall,
            "scores": scores,
            "collection": {
                "event_count": event_count,
                "article_count": article_count,
                "research_count": research_count,
                "news_context_pack_count": pack_count,
                "signal_count": signal_count,
                "performance_measurement_count": len(performance),
                "event_intake_audit_count": len(event_audits),
                "context_selection_audit_count": len(context_audits),
                "market_reaction_snapshot_count": len(market_reactions),
                "reconciliation_run_count": len(reconciliations),
                "market_data_connectivity_status": (market_data_report or {}).get("overall_status", "missing"),
                "latest_news_at": latest_news_at.strftime("%Y-%m-%dT%H:%M:%S") if latest_news_at else None,
                "latest_research_at": latest_research_at.strftime("%Y-%m-%dT%H:%M:%S") if latest_research_at else None,
                "latest_pack_at": latest_pack_at.strftime("%Y-%m-%dT%H:%M:%S") if latest_pack_at else None,
            },
            "quality": {
                "avg_articles_per_event": round(avg_articles_per_event, 2),
                "event_without_articles": event_without_articles,
                "orphan_event_ratio": round(orphan_event_ratio, 4),
                "article_url_duplicate_ratio": round(article_url_dupe_ratio, 4),
                "avg_ingest_delay_sec": avg_ingest_delay_sec,
                "article_tiers": article_tiers,
                "high_quality_article_ratio": round(high_quality_article_ratio, 4),
                "verification_coverage": round(verification_coverage, 4),
                "avg_evidence_per_signal": round(avg_evidence_per_signal, 2),
                "pack_states": pack_states,
                "avg_pack_score": avg_pack_score,
                "performance_coverage": round(performance_coverage, 4),
                "event_audit_coverage": round((len(event_audits) / max(1, event_count)), 4) if event_count else 0.0,
                "context_audit_coverage": round((len(context_audits) / max(1, pack_count)), 4) if pack_count else 0.0,
                "market_reaction_coverage": round((len(market_reactions) / max(1, signal_count)), 4) if signal_count else 0.0,
                "latest_reconciliation_status": reconciliations[0].get("status") if reconciliations else "missing",
                "market_data_connectivity_checks": len((market_data_report or {}).get("checks", [])),
                "checkpoint_failure_count": len(checkpoint_failures),
            },
            "recommendations": self._recommendations(scores, event_count, article_count, research_count, pack_count, verification_coverage, performance_coverage, high_quality_article_ratio, checkpoint_failures, len(event_audits), len(context_audits), market_data_report),
        }

    def render(self, report: dict[str, Any]) -> str:
        scores = report.get("scores", {}) or {}
        collection = report.get("collection", {}) or {}
        quality = report.get("quality", {}) or {}
        lines = [
            "🧪 **[데이터 품질 평가]**",
            f"- overall: {report.get('overall_score', 0.0):.1f}/100 | lookback={report.get('lookback_hours')}h",
            f"- freshness={scores.get('freshness', 0.0):.1f} coverage={scores.get('coverage', 0.0):.1f} "
            f"source={scores.get('source_quality', 0.0):.1f} verification={scores.get('verification', 0.0):.1f} "
            f"management={scores.get('management', 0.0):.1f} feedback={scores.get('performance_feedback', 0.0):.1f}",
            f"- market_reaction={scores.get('market_reaction', 0.0):.1f} reconciliation={scores.get('reconciliation', 0.0):.1f}",
            f"- market_data_connectivity={scores.get('market_data_connectivity', 0.0):.1f} status={collection.get('market_data_connectivity_status', 'missing')}",
            f"- events={collection.get('event_count', 0)} articles={collection.get('article_count', 0)} "
            f"research={collection.get('research_count', 0)} packs={collection.get('news_context_pack_count', 0)} "
            f"signals={collection.get('signal_count', 0)} measurements={collection.get('performance_measurement_count', 0)}",
            f"- audits: event_intake={collection.get('event_intake_audit_count', 0)} context_selection={collection.get('context_selection_audit_count', 0)} "
            f"market_reactions={collection.get('market_reaction_snapshot_count', 0)} reconciliations={collection.get('reconciliation_run_count', 0)}",
            f"- latest_news={collection.get('latest_news_at') or '-'} | latest_research={collection.get('latest_research_at') or '-'} | latest_pack={collection.get('latest_pack_at') or '-'}",
            f"- avg_articles/event={quality.get('avg_articles_per_event', 0.0)} | high_quality_articles={quality.get('high_quality_article_ratio', 0.0) * 100:.1f}% | "
            f"verified_signals={quality.get('verification_coverage', 0.0) * 100:.1f}% | performance_coverage={quality.get('performance_coverage', 0.0) * 100:.1f}%",
        ]
        recs = report.get("recommendations", []) or []
        if recs:
            lines.append("\n개선 권고:")
            for item in recs[:8]:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def save_report(self, report: dict[str, Any]) -> None:
        self.db.set_system_metadata("data_quality_report_v1", json.dumps(report, ensure_ascii=False))

    def _latest_market_data_report(self) -> dict[str, Any] | None:
        raw = self.db.get_system_metadata("market_data_connectivity_report_v1")
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def _market_data_connectivity_score(self, report: dict[str, Any] | None) -> float:
        if not report:
            return 0.0
        status = str(report.get("overall_status") or "").lower()
        if status == "ok":
            return 100.0
        if status == "warn":
            return 50.0
        return 0.0

    def _list_recent_packs(self, since_dt: datetime, limit: int = 1000) -> list[dict[str, Any]]:
        self.db.cursor.execute(
            '''
            SELECT generated_at, pack_json
            FROM news_context_packs
            WHERE generated_at >= ?
            ORDER BY generated_at DESC, id DESC
            LIMIT ?
            ''',
            (since_dt.strftime("%Y-%m-%dT%H:%M:%S"), int(limit)),
        )
        out = []
        for generated_at, pack_json in self.db.cursor.fetchall():
            try:
                pack = json.loads(pack_json) if pack_json else {}
            except Exception:
                pack = {}
            if isinstance(pack, dict):
                pack.setdefault("generated_at", generated_at)
                out.append(pack)
        return out

    def _list_checkpoints(self) -> list[dict[str, Any]]:
        self.db.cursor.execute(
            '''
            SELECT source, last_success_at, updated_at, last_attempt_at, last_status, last_error, last_item_count
            FROM news_ingest_checkpoints
            ORDER BY source ASC
            '''
        )
        return [
            {
                "source": row[0],
                "last_success_at": row[1],
                "updated_at": row[2],
                "last_attempt_at": row[3],
                "last_status": row[4] or "",
                "last_error": row[5] or "",
                "last_item_count": int(row[6] or 0),
            }
            for row in self.db.cursor.fetchall()
        ]

    def _article_tier_counts(self, articles: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for article in articles:
            tier = self._pack_service._source_tier(str(article.get("source") or ""), article.get("canonical_url") or article.get("url"))
            counts[tier] = counts.get(tier, 0) + 1
        return counts

    def _freshness_score(self, latest_at: datetime | None, now: datetime) -> float:
        if latest_at is None:
            return 0.0
        age_hours = max(0.0, (now - latest_at).total_seconds() / 3600.0)
        if age_hours <= 2:
            return 100.0
        if age_hours <= 12:
            return 85.0
        if age_hours <= 24:
            return 65.0
        if age_hours <= 72:
            return 40.0
        return 15.0

    def _coverage_score(self, event_count: int, avg_articles_per_event: float, research_count: int, pack_count: int, lookback_hours: int) -> float:
        day_count = max(1.0, lookback_hours / 24.0)
        target_events = max(5.0, day_count * 4.0)
        event_score = min(100.0, event_count / target_events * 100.0)
        article_score = min(100.0, avg_articles_per_event / 2.0 * 100.0)
        research_score = min(100.0, research_count / max(3.0, day_count * 2.0) * 100.0)
        pack_score = min(100.0, pack_count / max(2.0, day_count) * 100.0)
        return round(event_score * 0.35 + article_score * 0.25 + research_score * 0.25 + pack_score * 0.15, 2)

    def _management_score(self, orphan_event_ratio: float, article_url_dupe_ratio: float, checkpoint_failures: list[dict[str, Any]]) -> float:
        score = 100.0
        score -= min(40.0, orphan_event_ratio * 100.0 * 0.4)
        score -= min(25.0, article_url_dupe_ratio * 100.0 * 0.5)
        score -= min(35.0, len(checkpoint_failures) * 10.0)
        return round(max(0.0, score), 2)

    def _recommendations(
        self,
        scores: dict[str, float],
        event_count: int,
        article_count: int,
        research_count: int,
        pack_count: int,
        verification_coverage: float,
        performance_coverage: float,
        high_quality_article_ratio: float,
        checkpoint_failures: list[dict[str, Any]],
        event_audit_count: int,
        context_audit_count: int,
        market_data_report: dict[str, Any] | None,
    ) -> list[str]:
        recs = []
        if event_count == 0 or scores.get("freshness", 0.0) < 65:
            recs.append("뉴스 수집 작업(run_news)을 먼저 점검하세요. 최신 이벤트가 부족합니다.")
        if article_count == 0 or scores.get("coverage", 0.0) < 55:
            recs.append("뉴스 이벤트당 기사 연결과 News Context Pack 생성 주기를 늘려 coverage를 보강하세요.")
        if research_count == 0 or verification_coverage < 0.4:
            recs.append("시그널 후보에 대한 웹검증 예산 또는 공식소스 보강 루프를 늘리세요.")
        if high_quality_article_ratio < 0.35:
            recs.append("SEC/OpenDART/IR/Reuters 같은 high-quality source 비중이 낮습니다.")
        if pack_count == 0:
            recs.append("run_news_context 작업을 실행해 토론/RAG 입력용 뉴스팩을 생성하세요.")
        if performance_coverage < 0.5:
            recs.append("run_replay를 주기 실행해 시그널별 성과 측정치를 더 쌓으세요.")
        if not market_data_report:
            recs.append("run_market_data_check를 실행해 미국/한국/홍콩/일본 등 시장별 가격 조회 상태를 검증하세요.")
        elif str(market_data_report.get("overall_status") or "").lower() != "ok":
            recs.append("market_data_connectivity_report_v1의 실패/경고 항목을 보고 종목별 provider_ticker나 시장 힌트를 보정하세요.")
        if event_count > 0 and event_audit_count == 0:
            recs.append("이벤트 Intake 감사 로그가 없습니다. run_signals로 라우팅 이유를 남기세요.")
        if pack_count > 0 and context_audit_count == 0:
            recs.append("컨텍스트 선택 감사 로그가 없습니다. 뉴스팩/RAG/토론 경로를 실행해 AI 입력 선택 이유를 남기세요.")
        if checkpoint_failures:
            sources = ", ".join(str(c.get("source")) for c in checkpoint_failures[:5])
            recs.append(f"수집 체크포인트 실패 소스를 확인하세요: {sources}")
        if not recs:
            recs.append("현재 데이터 품질은 운영 가능한 범위입니다. 다음 단계는 실패 유형 taxonomy와 정책 피드백 표본 확대입니다.")
        return recs

    def _max_dt(self, values: list[Any]) -> datetime | None:
        parsed = [dt for dt in (self._parse_dt(v) for v in values) if dt is not None]
        return max(parsed) if parsed else None

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                return dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception:
            try:
                return datetime.strptime(str(value), "%Y-%m-%d")
            except Exception:
                return None

    def _duplicate_ratio(self, values: list[Any]) -> float:
        cleaned = [str(v).strip().lower() for v in values if str(v or "").strip()]
        if not cleaned:
            return 0.0
        return max(0.0, (len(cleaned) - len(set(cleaned))) / len(cleaned))
