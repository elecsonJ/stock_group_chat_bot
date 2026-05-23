import asyncio
import json
import os
import re
import sys
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db_manager import DBManager
from portfolio_manager import PortfolioManager


def score_debate_quality(full_log: str, event_id: str = "", debate_id: int | None = None) -> dict:
    text = str(full_log or "")
    checks = {
        "official_data": any(marker in text for marker in ["SEC EDGAR", "OpenDART", "공식 기업 데이터"]),
        "evidence_ids": bool(re.search(r"EV\d{4}|\[근거ID:", text)),
        "opposing_views": all(marker in text for marker in ["찬성", "반대"]) or "bearish" in text.lower() and "bullish" in text.lower(),
        "web_research": "Evidence Package" in text or "[SEARCH:" in text or "심층 리서치" in text,
        "final_verdict": "최종 판결" in text or "최종 결론" in text or "[최종 선택:" in text,
        "uncertainty": any(marker in text for marker in ["주의", "한계", "불확실", "확인 필요", "부족"]),
    }
    weights = {
        "official_data": 20,
        "evidence_ids": 20,
        "opposing_views": 15,
        "web_research": 15,
        "final_verdict": 20,
        "uncertainty": 10,
    }
    total = sum(weights[k] for k, passed in checks.items() if passed)
    if total >= 80:
        status = "strong"
    elif total >= 60:
        status = "usable"
    elif total >= 40:
        status = "weak"
    else:
        status = "poor"
    missing = [k for k, passed in checks.items() if not passed]
    return {
        "debate_id": debate_id,
        "event_id": event_id,
        "total_score": float(total),
        "status": status,
        "detail_json": {
            "checks": checks,
            "weights": weights,
            "missing": missing,
            "log_chars": len(text),
        },
    }


class StdoutCtx:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix.strip()

    async def send(self, text: str):
        msg = str(text or "")
        if self.prefix:
            print(f"[{self.prefix}] {msg}")
        else:
            print(msg)


class DiscordWebhookCtx:
    def __init__(self, webhook_url: str, prefix: str = "", max_chars: int = 1900):
        self.webhook_url = webhook_url.strip()
        self.prefix = prefix.strip()
        self.max_chars = max(500, min(1900, int(max_chars)))

    async def send(self, text: str):
        msg = str(text or "")
        if self.prefix:
            msg = f"[{self.prefix}] {msg}"
        for i in range(0, len(msg), self.max_chars):
            chunk = msg[i:i + self.max_chars]
            await asyncio.to_thread(self._post_sync, chunk)

    def _post_sync(self, content: str):
        payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if getattr(resp, "status", 204) not in {200, 204}:
                    print(f"[discord-webhook] unexpected status={getattr(resp, 'status', None)}")
        except Exception as exc:
            print(f"[discord-webhook] send failed: {exc}")


class FanoutCtx:
    def __init__(self, *contexts):
        self.contexts = [ctx for ctx in contexts if ctx is not None]

    async def send(self, text: str):
        for ctx in self.contexts:
            await ctx.send(text)


class DebateQueueRunner:
    def __init__(
        self,
        db: DBManager | None = None,
        portfolio_manager: PortfolioManager | None = None,
        controller_factory=None,
    ):
        self.db = db or DBManager()
        self.portfolio_manager = portfolio_manager or PortfolioManager()
        self.controller_factory = controller_factory or self._default_controller_factory

    def _default_controller_factory(self):
        from crawler import InvestmentCrawler
        from debate_manager import DebateController
        from llm_client import LLMClientManager
        from stable_web_search_agent import FactCheckAgent

        llm = LLMClientManager()
        checker = FactCheckAgent(llm)
        crawler = InvestmentCrawler()
        return DebateController(llm, checker, crawler)

    def _build_portfolio_context(self) -> str:
        raw_portfolio = self.portfolio_manager.load_raw_portfolio() or ""
        if not raw_portfolio:
            return ""
        holdings, _warnings = self.portfolio_manager.parse_holdings(raw_portfolio)
        holdings_agg = self.portfolio_manager.aggregate_holdings(holdings)
        return self.portfolio_manager.build_llm_context(holdings_agg, raw_text=raw_portfolio)

    async def run_once(self) -> dict | None:
        item = self.db.claim_next_debate_queue_item()
        if not item:
            return None

        topic = str(item.get("topic") or "").strip()
        if not topic:
            event = self.db.get_signal_event(str(item.get("event_id") or ""))
            if event:
                topic = f"{event.get('title', '')}\n\n{event.get('summary', '')}".strip()
        if not topic:
            self.db.complete_debate_queue_item(
                int(item["id"]),
                status="skipped",
                note="empty_topic",
            )
            return {
                "status": "skipped",
                "queue_id": item.get("id"),
                "event_id": item.get("event_id"),
                "reason": "empty_topic",
            }

        portfolio_context = self._build_portfolio_context()
        controller = self.controller_factory()
        prefix = str(item.get("event_id") or item.get("id") or "debate")
        stdout_ctx = StdoutCtx(prefix=prefix)
        webhook_url = os.getenv("DISCORD_DEBATE_WEBHOOK_URL", "").strip()
        webhook_ctx = None
        if webhook_url:
            webhook_ctx = DiscordWebhookCtx(
                webhook_url=webhook_url,
                prefix=prefix,
                max_chars=int(os.getenv("DISCORD_DEBATE_WEBHOOK_CHUNK_CHARS", "1800")),
            )
        ctx = FanoutCtx(stdout_ctx, webhook_ctx)
        try:
            await ctx.send(
                "🚦 **[자동 토론 시작]**\n"
                f"- queue_id={item.get('id')}\n"
                f"- priority={item.get('priority')} | reason={item.get('reason') or '-'}\n"
                f"- topic={topic[:500]}"
            )
            history, debate_id = await controller.run_full_debate(
                ctx,
                topic,
                portfolio_context=portfolio_context,
            )
            quality = score_debate_quality(
                history,
                event_id=str(item.get("event_id") or ""),
                debate_id=int(debate_id),
            )
            saved_quality = self.db.save_debate_quality_score(quality)
            self.db.complete_debate_queue_item(
                int(item["id"]),
                status="completed",
                debate_id=int(debate_id),
                note="auto_debate_completed",
            )
            await ctx.send(
                f"✅ **[자동 토론 완료]** debate_id={int(debate_id)}\n"
                f"- quality={saved_quality['status']} score={saved_quality['total_score']:.1f}"
            )
            return {
                "status": "completed",
                "queue_id": item.get("id"),
                "event_id": item.get("event_id"),
                "debate_id": int(debate_id),
                "quality": saved_quality,
            }
        except Exception as exc:
            self.db.complete_debate_queue_item(
                int(item["id"]),
                status="failed",
                note=str(exc)[:500],
            )
            await ctx.send(f"❌ **[자동 토론 실패]** reason={str(exc)[:500]}")
            return {
                "status": "failed",
                "queue_id": item.get("id"),
                "event_id": item.get("event_id"),
                "reason": str(exc),
            }

    async def run_batch(self, max_items: int = 1) -> list[dict]:
        results = []
        for _ in range(max(1, int(max_items))):
            result = await self.run_once()
            if not result:
                break
            results.append(result)
        return results


async def main():
    db = DBManager()
    runner = DebateQueueRunner(db=db)
    max_items = max(1, int(os.getenv("DEBATE_JOB_MAX_ITEMS", "2")))
    results = await runner.run_batch(max_items=max_items)
    pending = db.list_debate_queue(limit=20, statuses=["pending"])

    print("=== 자동 토론 큐 배치 ===")
    print(f"- processed: {len(results)}")
    print(f"- pending: {len(pending)}")
    for result in results:
        line = f"  * {result.get('event_id')} status={result.get('status')}"
        if result.get("debate_id"):
            line += f" debate_id={result.get('debate_id')}"
        if result.get("reason"):
            line += f" reason={result.get('reason')}"
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
