from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from db_manager import DBManager


class PerformanceTracker:
    def __init__(self, db: DBManager | None = None):
        self.db = db or DBManager()

    def record_measurement(
        self,
        *,
        event_id: str,
        ticker: str,
        horizon: str,
        entry_price: float,
        exit_price: float,
        side: str = "BUY",
        benchmark_ticker: str | None = None,
        benchmark_entry_price: float | None = None,
        benchmark_exit_price: float | None = None,
        source: str = "replay",
        detail_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_side = str(side or "BUY").upper().strip()
        if entry_price <= 0 or exit_price <= 0:
            raise ValueError("entry_price and exit_price must be positive")

        if normalized_side == "SELL":
            return_pct = ((entry_price - exit_price) / entry_price) * 100.0
        else:
            return_pct = ((exit_price - entry_price) / entry_price) * 100.0

        benchmark_return_pct = 0.0
        if benchmark_entry_price and benchmark_entry_price > 0 and benchmark_exit_price and benchmark_exit_price > 0:
            benchmark_return_pct = ((benchmark_exit_price - benchmark_entry_price) / benchmark_entry_price) * 100.0
        alpha_pct = return_pct - benchmark_return_pct
        row = {
            "event_id": event_id,
            "ticker": ticker,
            "horizon": horizon,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "return_pct": round(return_pct, 4),
            "benchmark_ticker": benchmark_ticker,
            "benchmark_return_pct": round(benchmark_return_pct, 4),
            "alpha_pct": round(alpha_pct, 4),
            "measured_at": (detail_json or {}).get("measured_at"),
            "source": source,
            "detail_json": {
                "side": normalized_side,
                **(detail_json or {}),
            },
        }
        self.db.save_signal_performance(row)
        return row

    def record_attributions(
        self,
        *,
        signal_event: dict[str, Any] | None,
        measurement: dict[str, Any],
        source: str = "replay",
    ) -> list[dict[str, Any]]:
        if not signal_event:
            return []
        event_id = str(measurement.get("event_id", "") or signal_event.get("event_id", ""))
        ticker = str(measurement.get("ticker", "") or "")
        horizon = str(measurement.get("horizon", "") or "")
        measured_at = measurement.get("measured_at")
        return_pct = float(measurement.get("return_pct", 0.0) or 0.0)
        alpha_pct = float(measurement.get("alpha_pct", 0.0) or 0.0)
        verification = signal_event.get("verification_json", {}) or {}
        score_json = signal_event.get("score_json", {}) or {}

        payloads: list[tuple[str, str, float, dict[str, Any]]] = []
        direction = str(signal_event.get("direction", "") or "").strip()
        urgency = str(signal_event.get("urgency", "") or "").strip()
        verdict = str(verification.get("verdict", "") or "").strip()
        if direction:
            payloads.append(("direction", direction, 1.0, {}))
        if urgency:
            payloads.append(("urgency", urgency, 1.0, {}))
        if verdict:
            payloads.append(("verification_verdict", verdict, 1.0, {}))

        evidence_count = int(verification.get("evidence_count", 0) or 0)
        payloads.append(("evidence_count_bucket", self._bucket_evidence_count(evidence_count), 1.0, {"evidence_count": evidence_count}))
        payloads.append(("signal_score_bucket", self._bucket_score(float(signal_event.get("score_total", 0.0) or 0.0)), 1.0, {}))
        payloads.append(("signal_confidence_bucket", self._bucket_confidence(float(signal_event.get("confidence", 0.0) or 0.0)), 1.0, {}))
        payloads.append(("signal_status", str(signal_event.get("status", "") or "unknown"), 1.0, {}))
        payloads.append(("portfolio_hit", str(bool(score_json.get("portfolio_hit", False))).lower(), 1.0, {}))

        base_score = float(score_json.get("base_score", 0.0) or 0.0)
        impact_score = float(score_json.get("impact_score", 0.0) or 0.0)
        payloads.append(("signal_base_score_bucket", self._bucket_score(base_score), 1.0, {"base_score": base_score}))
        payloads.append(("impact_score_bucket", self._bucket_impact_score(impact_score), 1.0, {"impact_score": impact_score}))

        for domain in list(dict.fromkeys(verification.get("domains", [])[:8])):
            payloads.append(("verification_domain", str(domain), 1.0, {}))
        for tier in list(dict.fromkeys(verification.get("source_tiers", [])[:5])):
            payloads.append(("source_tier", str(tier), 1.0, {}))

        for impact_kw in list(dict.fromkeys(score_json.get("impact_keywords", [])[:8])):
            payloads.append(("impact_keyword", str(impact_kw), 1.0, {}))

        detail = measurement.get("detail_json", {}) or {}
        exit_reason = str(detail.get("exit_reason", "") or "").strip()
        origin = str(detail.get("origin", "") or "").strip()
        if exit_reason:
            payloads.append(("exit_reason", exit_reason, 1.0, {}))
        if origin:
            payloads.append(("entry_origin", origin, 1.0, {}))

        queue_item = self.db.get_debate_queue_item(event_id) if event_id else None
        if queue_item:
            if queue_item.get("reason"):
                payloads.append(("debate_queue_reason", str(queue_item.get("reason")), 1.0, {}))
            if queue_item.get("cost_gate_status"):
                payloads.append(("debate_cost_gate_status", str(queue_item.get("cost_gate_status")), 1.0, {}))
            payloads.append(("debate_priority_bucket", self._bucket_score(float(queue_item.get("priority", 0) or 0)), 1.0, {}))
            trigger_json = queue_item.get("trigger_json", {}) or {}
            hidden_candidates = trigger_json.get("hidden_candidates", []) or []
            if hidden_candidates:
                best_validation = max(float(c.get("validation_score", 0.0) or 0.0) for c in hidden_candidates)
                best_path = max(float(c.get("path_score", 0.0) or 0.0) for c in hidden_candidates)
                payloads.append(("hidden_candidate_validation_bucket", self._bucket_confidence(best_validation), 1.0, {"best_validation_score": best_validation}))
                payloads.append(("hidden_candidate_path_bucket", self._bucket_confidence(best_path), 1.0, {"best_path_score": best_path}))

        debate_quality = self.db.get_latest_debate_quality_for_event(event_id) if event_id else None
        if debate_quality:
            payloads.append(("debate_quality_status", str(debate_quality.get("status", "")), 1.0, {}))
            payloads.append(("debate_quality_score_bucket", self._bucket_score(float(debate_quality.get("total_score", 0.0) or 0.0)), 1.0, {}))
            detail = debate_quality.get("detail_json", {}) or {}
            for missing in (detail.get("missing", []) or [])[:8]:
                payloads.append(("debate_quality_missing", str(missing), 1.0, {}))

        reaction_rows = self.db.list_market_reaction_snapshots(event_id=event_id, ticker=ticker, limit=1) if event_id and ticker else []
        if reaction_rows:
            reaction = reaction_rows[0]
            payloads.append(("market_reaction_quality", str(reaction.get("market_data_quality", "unknown")), 1.0, {}))
            payloads.append(("already_priced_in", str(bool(reaction.get("already_priced_in", False))).lower(), 1.0, {}))
            relative_move = float(reaction.get("relative_post_60m_pct", 0.0) or 0.0)
            pre_move = float(reaction.get("pre_news_move_pct", 0.0) or 0.0)
            payloads.append(("relative_post_60m_bucket", self._bucket_signed_pct(relative_move), 1.0, {"relative_post_60m_pct": relative_move}))
            payloads.append(("pre_news_move_bucket", self._bucket_signed_pct(pre_move), 1.0, {"pre_news_move_pct": pre_move}))

        saved = []
        for category, label, weight, extra in payloads:
            row = {
                "event_id": event_id,
                "ticker": ticker,
                "horizon": horizon,
                "category": category,
                "label": label,
                "weight": weight,
                "return_pct": return_pct,
                "alpha_pct": alpha_pct,
                "measured_at": measured_at,
                "source": source,
                "detail_json": extra,
            }
            self.db.save_signal_attribution(row)
            saved.append(row)
        return saved

    def summarize(
        self,
        *,
        event_id: str | None = None,
        horizon: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        rows = self.db.list_signal_performance(event_id=event_id, limit=limit)
        if horizon:
            rows = [r for r in rows if str(r.get("horizon", "")) == horizon]
        deduped = self._dedupe_latest(rows)
        if not deduped:
            return {
                "count": 0,
                "win_rate": 0.0,
                "avg_return_pct": 0.0,
                "avg_alpha_pct": 0.0,
                "expectancy_pct": 0.0,
                "profit_factor": 0.0,
            }

        returns = [float(r.get("return_pct", 0.0)) for r in deduped]
        alphas = [float(r.get("alpha_pct", 0.0)) for r in deduped]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        win_rate = len(wins) / len(returns)
        loss_rate = len(losses) / len(returns)
        expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf" if gross_win > 0 else 0.0)
        return {
            "count": len(deduped),
            "win_rate": round(win_rate, 4),
            "avg_return_pct": round(sum(returns) / len(returns), 4),
            "avg_alpha_pct": round(sum(alphas) / len(alphas), 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "expectancy_pct": round(expectancy, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        }

    def build_equity_curve(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        event_id: str | None = None,
        horizon: str | None = None,
        starting_equity: float = 100000.0,
        use_alpha: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        if rows is None:
            rows = self.db.list_signal_performance(event_id=event_id, limit=limit)
        if horizon:
            rows = [r for r in rows if str(r.get("horizon", "")) == horizon]
        deduped = sorted(self._dedupe_latest(rows), key=self._row_sort_key)

        equity = float(starting_equity)
        peak = equity
        max_drawdown_pct = 0.0
        points = []
        for row in deduped:
            metric = float(row.get("alpha_pct" if use_alpha else "return_pct", 0.0) or 0.0)
            equity *= 1.0 + (metric / 100.0)
            peak = max(peak, equity)
            drawdown_pct = ((equity - peak) / peak * 100.0) if peak > 0 else 0.0
            max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)
            points.append(
                {
                    "measured_at": row.get("measured_at"),
                    "event_id": row.get("event_id"),
                    "ticker": row.get("ticker"),
                    "equity": round(equity, 4),
                    "drawdown_pct": round(drawdown_pct, 4),
                    "metric_pct": round(metric, 4),
                }
            )

        total_return_pct = ((equity - starting_equity) / starting_equity * 100.0) if starting_equity > 0 else 0.0
        return {
            "points": points,
            "ending_equity": round(equity, 4),
            "total_return_pct": round(total_return_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "count": len(points),
        }

    def summarize_attributions(
        self,
        *,
        category: str,
        horizon: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows = self.db.list_signal_attributions(category=category, horizon=horizon, limit=limit)
        bucket: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get("label", "") or "").strip()
            if not label:
                continue
            item = bucket.setdefault(
                label,
                {
                    "label": label,
                    "count": 0,
                    "wins": 0,
                    "sum_return": 0.0,
                    "sum_alpha": 0.0,
                },
            )
            ret = float(row.get("return_pct", 0.0) or 0.0)
            alpha = float(row.get("alpha_pct", 0.0) or 0.0)
            item["count"] += 1
            item["wins"] += 1 if ret > 0 else 0
            item["sum_return"] += ret
            item["sum_alpha"] += alpha

        out = []
        for item in bucket.values():
            count = item["count"]
            out.append(
                {
                    "label": item["label"],
                    "count": count,
                    "win_rate": round(item["wins"] / count, 4) if count else 0.0,
                    "avg_return_pct": round(item["sum_return"] / count, 4) if count else 0.0,
                    "avg_alpha_pct": round(item["sum_alpha"] / count, 4) if count else 0.0,
                }
            )
        return sorted(out, key=lambda x: (x["avg_alpha_pct"], x["count"]), reverse=True)

    def build_feedback_report(
        self,
        *,
        horizon: str | None = "1d",
        min_samples: int = 3,
        limit: int = 2000,
    ) -> dict[str, Any]:
        categories = [
            "signal_score_bucket",
            "signal_confidence_bucket",
            "evidence_count_bucket",
            "verification_verdict",
            "source_tier",
            "impact_keyword",
            "portfolio_hit",
            "entry_origin",
            "exit_reason",
            "debate_queue_reason",
            "debate_cost_gate_status",
            "hidden_candidate_validation_bucket",
            "debate_quality_status",
            "debate_quality_missing",
            "market_reaction_quality",
            "already_priced_in",
            "relative_post_60m_bucket",
            "pre_news_move_bucket",
        ]
        overall = self.summarize(horizon=horizon, limit=limit)
        category_reports: dict[str, Any] = {}
        promote: list[dict[str, Any]] = []
        caution: list[dict[str, Any]] = []

        for category in categories:
            rows = self.summarize_attributions(category=category, horizon=horizon, limit=limit)
            usable = [r for r in rows if int(r.get("count", 0) or 0) >= min_samples]
            positive = [r for r in usable if float(r.get("avg_alpha_pct", 0.0) or 0.0) > 0]
            negative = [r for r in usable if float(r.get("avg_alpha_pct", 0.0) or 0.0) < 0]
            category_reports[category] = {
                "top": positive[:5],
                "bottom": sorted(negative, key=lambda x: (x["avg_alpha_pct"], -x["count"]))[:5],
                "sample_count": sum(int(r.get("count", 0) or 0) for r in rows),
                "qualified_label_count": len(usable),
            }
            for row in positive[:3]:
                if float(row.get("avg_alpha_pct", 0.0) or 0.0) >= 0.5:
                    promote.append({"category": category, **row})
            for row in sorted(negative, key=lambda x: (x["avg_alpha_pct"], -x["count"]))[:3]:
                if float(row.get("avg_alpha_pct", 0.0) or 0.0) <= -0.5:
                    caution.append({"category": category, **row})

        report = {
            "schema_version": "performance_feedback.v1",
            "generated_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            "horizon": horizon,
            "min_samples": int(min_samples),
            "overall": overall,
            "categories": category_reports,
            "policy_hints": {
                "promote": sorted(promote, key=lambda x: (x["avg_alpha_pct"], x["count"]), reverse=True)[:12],
                "caution": sorted(caution, key=lambda x: (x["avg_alpha_pct"], -x["count"]))[:12],
            },
        }
        return report

    def save_feedback_profile(self, report: dict[str, Any]) -> None:
        self.db.set_system_metadata(
            "performance_feedback_profile_v1",
            json.dumps(report, ensure_ascii=False),
        )

    def render_feedback_report(self, report: dict[str, Any]) -> str:
        overall = report.get("overall", {}) or {}
        lines = [
            "🧭 **[성과 기반 피드백]**",
            f"- horizon: {report.get('horizon') or '-'} | min_samples={report.get('min_samples')}",
            f"- count: {overall.get('count', 0)} | win_rate={overall.get('win_rate', 0.0) * 100:.1f}% | "
            f"avg_alpha={overall.get('avg_alpha_pct', 0.0):+.2f}% | expectancy={overall.get('expectancy_pct', 0.0):+.2f}%",
        ]
        hints = report.get("policy_hints", {}) or {}
        promote = hints.get("promote", []) or []
        caution = hints.get("caution", []) or []
        if promote:
            lines.append("\n강화 후보:")
            for row in promote[:6]:
                lines.append(
                    f"- {row.get('category')}={row.get('label')} | n={row.get('count')} "
                    f"alpha={row.get('avg_alpha_pct', 0.0):+.2f}% win={row.get('win_rate', 0.0) * 100:.1f}%"
                )
        if caution:
            lines.append("\n주의 후보:")
            for row in caution[:6]:
                lines.append(
                    f"- {row.get('category')}={row.get('label')} | n={row.get('count')} "
                    f"alpha={row.get('avg_alpha_pct', 0.0):+.2f}% win={row.get('win_rate', 0.0) * 100:.1f}%"
                )
        if not promote and not caution:
            lines.append("\n아직 표본이 부족합니다. replay 측정치를 더 쌓은 뒤 정책 힌트를 생성할 수 있습니다.")
        return "\n".join(lines)

    def save_run_summary(
        self,
        *,
        run_name: str,
        split_label: str,
        horizon: str,
        rows: list[dict[str, Any]],
        window_start: str | None = None,
        window_end: str | None = None,
        signal_count: int | None = None,
        starting_equity: float = 100000.0,
    ) -> dict[str, Any]:
        summary = self.summarize_rows(rows)
        curve = self.build_equity_curve(rows=rows, horizon=horizon, starting_equity=starting_equity)
        row = {
            "run_name": run_name,
            "split_label": split_label,
            "horizon": horizon,
            "window_start": window_start,
            "window_end": window_end,
            "signal_count": int(signal_count if signal_count is not None else len({r.get('event_id') for r in rows})),
            "measurement_count": int(summary["count"]),
            "win_rate": summary["win_rate"],
            "avg_return_pct": summary["avg_return_pct"],
            "avg_alpha_pct": summary["avg_alpha_pct"],
            "expectancy_pct": summary["expectancy_pct"],
            "profit_factor": summary["profit_factor"] if summary["profit_factor"] != float("inf") else 9999.0,
            "total_return_pct": curve["total_return_pct"],
            "max_drawdown_pct": curve["max_drawdown_pct"],
            "created_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            "detail_json": {
                "equity_curve_points": curve["points"][:200],
            },
        }
        self.db.save_performance_run_summary(row)
        return row

    def summarize_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        deduped = self._dedupe_latest(rows)
        if not deduped:
            return {
                "count": 0,
                "win_rate": 0.0,
                "avg_return_pct": 0.0,
                "avg_alpha_pct": 0.0,
                "expectancy_pct": 0.0,
                "profit_factor": 0.0,
            }
        returns = [float(r.get("return_pct", 0.0) or 0.0) for r in deduped]
        alphas = [float(r.get("alpha_pct", 0.0) or 0.0) for r in deduped]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        win_rate = len(wins) / len(returns)
        loss_rate = len(losses) / len(returns)
        expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf" if gross_win > 0 else 0.0)
        return {
            "count": len(deduped),
            "win_rate": round(win_rate, 4),
            "avg_return_pct": round(sum(returns) / len(returns), 4),
            "avg_alpha_pct": round(sum(alphas) / len(alphas), 4),
            "expectancy_pct": round(expectancy, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        }

    def _bucket_evidence_count(self, evidence_count: int) -> str:
        if evidence_count >= 6:
            return "6+"
        if evidence_count >= 3:
            return "3-5"
        if evidence_count >= 1:
            return "1-2"
        return "0"

    def _bucket_score(self, score: float) -> str:
        if score >= 85:
            return "85+"
        if score >= 75:
            return "75-84"
        if score >= 65:
            return "65-74"
        if score >= 55:
            return "55-64"
        if score > 0:
            return "1-54"
        return "0"

    def _bucket_confidence(self, value: float) -> str:
        if value >= 0.85:
            return "0.85+"
        if value >= 0.65:
            return "0.65-0.84"
        if value >= 0.45:
            return "0.45-0.64"
        if value > 0:
            return "0.01-0.44"
        return "0"

    def _bucket_impact_score(self, score: float) -> str:
        if score >= 20:
            return "20+"
        if score >= 10:
            return "10-19"
        if score > 0:
            return "1-9"
        return "0"

    def _bucket_signed_pct(self, value: float) -> str:
        if value >= 5:
            return "up_5pct_plus"
        if value >= 2:
            return "up_2pct_plus"
        if value > 0.5:
            return "up_small"
        if value <= -5:
            return "down_5pct_plus"
        if value <= -2:
            return "down_2pct_plus"
        if value < -0.5:
            return "down_small"
        return "flat"

    def _dedupe_latest(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            detail = row.get("detail_json") or {}
            side = str(detail.get("side", "BUY")).upper().strip()
            key = (
                str(row.get("event_id", "")),
                str(row.get("ticker", "")),
                str(row.get("horizon", "")),
                side,
            )
            latest[key] = row
        return list(latest.values())

    def _row_sort_key(self, row: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(row.get("measured_at", "") or ""),
            str(row.get("event_id", "") or ""),
            str(row.get("ticker", "") or ""),
        )
