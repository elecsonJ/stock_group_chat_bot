import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# 사용자 커스텀 모듈
from llm_client import LLMClientManager
from crawler import InvestmentCrawler
from web_search_agent import FactCheckAgent
from debate_manager import DebateController
from db_manager import DBManager
from data_fetcher.premium_crawler import PremiumCrawler
from rag_agent import RAGAgent
from portfolio_manager import PortfolioManager
from signal_engine import SignalEngine
from trading_executor import TradingExecutor
from performance_tracker import PerformanceTracker
from debate_job import score_debate_quality

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True  # 봇이 메시지를 읽기 위해 필수
bot = commands.Bot(command_prefix='!', intents=intents)

llm_manager = LLMClientManager()
crawler = InvestmentCrawler()
fact_checker = FactCheckAgent(llm_manager)
db_manager = DBManager()
rag_agent = RAGAgent(llm_manager)
portfolio_manager = PortfolioManager()
signal_engine = SignalEngine(db_manager)
trading_executor = TradingExecutor(db_manager)
performance_tracker = PerformanceTracker(db_manager)

# 채널별 대화 기록(Context)을 저장하는 딕셔너리
channel_memory = {}
channel_portfolio_context = {}
MAX_CHANNEL_HISTORY_CHARS = 120000

def is_model_error_text(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return True
    return lowered.startswith("error from gpt") or "circuitopen" in lowered


async def send_chunked(channel, text: str, chunk_size: int = 1800):
    txt = str(text or "")
    if len(txt) <= chunk_size:
        await channel.send(txt)
        return
    for i in range(0, len(txt), chunk_size):
        await channel.send(txt[i:i + chunk_size])

# 봇이 준비되었을 때
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    try:
        db_manager.purge_old_data(retention_days=180)
        print("[DB] 오래된 daily_news/research_evidences 정리 완료 (180일)")
    except Exception as e:
        print(f"[DB] 보존 정책 정리 실패: {e}")

@bot.command()
async def 뉴스(ctx):
    """
    오늘 저장된 프리미엄 뉴스를 보기 좋게 출력합니다.
    """
    # 1) 구조화 이벤트(DB) 우선 출력
    try:
        events = db_manager.get_latest_news_events(limit=12)
    except Exception:
        events = []

    if events:
        lines = ["📰 **[최신 구조화 뉴스 이벤트 브리핑]**"]
        for idx, e in enumerate(events, 1):
            lines.append(
                f"{idx}. {e.get('title')} ({e.get('date')}) "
                f"[conf={e.get('confidence')} / sources={e.get('source_count')} / articles={e.get('article_count')}]"
            )
            lines.append(f"   요약: {e.get('summary')}")
            for u in (e.get("sample_urls") or [])[:2]:
                lines.append(f"   - {u}")

        msg = "\n".join(lines)
        if len(msg) <= 1900:
            await ctx.send(msg)
        else:
            for i in range(0, len(msg), 1800):
                await ctx.send(msg[i:i + 1800])
        return

    # 2) 기존 파일 아카이브 fallback
    import glob
    news_dir = os.path.join(os.path.dirname(__file__), "..", "news_archive")
    files = glob.glob(os.path.join(news_dir, "premium_news_*.txt"))
    
    if not files:
        await ctx.send("⚠️ 오늘 수집된 프리미엄 뉴스가 없습니다. 윈도우 스케줄러를 확인해주세요.")
        return
    
    files.sort(reverse=True)
    latest_file = files[0]
    
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            news_content = f.read()
    except Exception as e:
        await ctx.send(f"⚠️ 뉴스 파일을 읽는 중 오류가 발생했습니다: {e}")
        return

    # 디스코드 메시지 2000자 제한을 피하기 위한 청크 분할 전송
    await ctx.send(f"📰 **[{os.path.basename(latest_file)}] 오늘의 글로벌 프리미엄 뉴스 브리핑**")
    
    news_len = len(news_content)
    chunks = []
    chunk_size = 1800
    for i in range(0, news_len, chunk_size):
        end_idx = min(i + chunk_size, news_len)
        chunks.append(news_content[i:end_idx])
        
    for chunk in chunks:
        await ctx.send(f"```text\n{chunk}\n```")

@bot.command()
async def 질문(ctx, *, user_query: str):
    """
    과거 AI 토론 기록(DB)을 뒤져서 질문에 대답하는 하이브리드 RAG 챗봇 기능
    사용 예시: !질문 어제 엔비디아에 대해 봇들이 뭐라고 결론 내렸어?
    """
    await ctx.send("🔍 **[기억 탐색 중...] 과거 회의록과 요약본 DB에서 관련 맥락을 추출하고 있습니다.**")
    
    try:
        answer = await rag_agent.answer_question(user_query)
        
        ans_len = len(answer)
        chunk_size = 1800
        chunks = []
        for i in range(0, ans_len, chunk_size):
            end_idx = min(i + chunk_size, ans_len)
            chunks.append(answer[i:end_idx])
            
        for chunk in chunks:
            await ctx.send(chunk)
    except Exception as e:
        await ctx.send(f"⚠️ RAG 검색 중 오류 발생: {e}")


@bot.command(name="포트폴리오")
async def portfolio_cmd(ctx):
    """
    로컬 포트폴리오 파일을 로드/파싱해 표시하고, 이후 !토론에서 LLM 컨텍스트로 주입합니다.
    """
    raw = portfolio_manager.load_raw_portfolio()
    if raw is None:
        await ctx.send(
            f"⚠️ 포트폴리오 파일이 없습니다: `{portfolio_manager.file_path}`\n"
            "파일을 생성한 뒤 다시 시도하세요."
        )
        return
    holdings, warnings = portfolio_manager.parse_holdings(raw)
    holdings_agg = portfolio_manager.aggregate_holdings(holdings)
    context = portfolio_manager.build_llm_context(holdings_agg, raw_text=raw)
    channel_portfolio_context[ctx.channel.id] = context
    report = portfolio_manager.render_portfolio_text(raw, holdings_agg, warnings)
    report += "\n\n✅ 이 채널의 다음 `!토론`부터 포트폴리오 컨텍스트가 자동 주입됩니다."
    await send_chunked(ctx, report)


@bot.command(name="포트변동")
async def portfolio_change_cmd(ctx):
    """
    포트폴리오 기준 현재 변동(PnL)을 계산해 출력합니다.
    """
    raw = portfolio_manager.load_raw_portfolio()
    if raw is None:
        await ctx.send(f"⚠️ 포트폴리오 파일이 없습니다: `{portfolio_manager.file_path}`")
        return
    holdings, warnings = portfolio_manager.parse_holdings(raw)
    holdings_agg = portfolio_manager.aggregate_holdings(holdings)
    if not holdings_agg:
        base_report = portfolio_manager.render_portfolio_text(raw, holdings_agg, warnings)
        await send_chunked(ctx, f"{base_report}\n\nℹ️ 변동 계산 가능한 보유 종목이 없습니다.")
        return
    snapshot = await portfolio_manager.get_variation_snapshot(holdings_agg)
    text = portfolio_manager.render_variation_text(snapshot)
    if warnings:
        text += f"\n\n⚠️ 파싱 경고: {' | '.join(warnings[:3])}"
    await send_chunked(ctx, text)


@bot.command(name="시그널")
async def signal_cmd(ctx):
    """
    최신 뉴스 이벤트를 단기 시그널로 점수화하고 승인대기 목록을 생성합니다.
    """
    raw = portfolio_manager.load_raw_portfolio() or ""
    holdings, _ = portfolio_manager.parse_holdings(raw) if raw else ([], [])
    holdings_agg = portfolio_manager.aggregate_holdings(holdings)
    portfolio_tickers = [h["ticker"] for h in holdings_agg]

    signal_threshold = float(os.getenv("SIGNAL_MIN_SCORE", "58"))
    signal_max_events = int(os.getenv("SIGNAL_MAX_EVENTS", "20"))
    verify_new_only = os.getenv("SIGNAL_VERIFY_NEW_ONLY", "true").strip().lower() not in {"0", "false", "no"}
    created = await signal_engine.generate_signals_from_news(
        portfolio_tickers=portfolio_tickers,
        max_events=signal_max_events,
        threshold=signal_threshold,
        checker=fact_checker,
        verify_new_only=verify_new_only,
    )
    created_new = sum(1 for c in created if c.get("new"))
    actionable = sum(1 for c in created if c.get("status") == "pending_approval")
    debate_queued = sum(1 for c in created if (c.get("debate_queue") or {}).get("created") or (c.get("debate_queue") or {}).get("merged"))
    review_triggered = sum(len(c.get("review_triggers", [])) for c in created)
    header = (
        "⚡ **[단기 시그널 생성 완료]**\n"
        f"- 신규 이벤트: {created_new}\n"
        f"- 생성 이벤트: {len(created)}\n"
        f"- 승인대기: {actionable}\n\n"
        f"- 자동 토론 큐 반영: {debate_queued}\n"
        f"- 투자 변화 트리거: {review_triggered}\n\n"
    )
    body = signal_engine.render_signal_list_text(limit=12)
    await send_chunked(ctx, header + body)


@bot.command(name="시그널상세")
async def signal_detail_cmd(ctx, event_id: str):
    """
    시그널 상세(점수/추천/승인상태)를 조회합니다.
    """
    txt = signal_engine.render_signal_detail_text(event_id.strip().upper())
    await send_chunked(ctx, txt)


@bot.command(name="승인목록")
async def approval_list_cmd(ctx):
    db_manager.mark_expired_approvals()
    rows = db_manager.list_pending_approvals(limit=15)
    if not rows:
        await ctx.send("📭 현재 승인 대기 이벤트가 없습니다.")
        return
    lines = ["📝 **[승인 대기 목록]**"]
    for r in rows:
        lines.append(
            f"- `{r['event_id']}` | score={r['score_total']:.1f} | {r['direction']}/{r['urgency']} | expires={r['expires_at'] or '-'}"
        )
        lines.append(f"  {r['title']}")
    await send_chunked(ctx, "\n".join(lines))


@bot.command(name="토론큐")
async def debate_queue_cmd(ctx):
    rows = db_manager.list_debate_queue(limit=15, statuses=["needs_review", "pending", "processing"])
    if not rows:
        await ctx.send("📭 현재 자동 토론 큐가 비어 있습니다.")
        return
    lines = ["🧠 **[자동 토론 큐]**"]
    for row in rows:
        lines.append(
            f"- `{row['event_id']}` | {row['status']} | priority={row['priority']} | "
            f"{row['direction']}/{row['urgency']} | ticker={row.get('ticker') or '-'}"
        )
        gate = row.get("cost_gate_status") or "-"
        lines.append(f"  reason={row.get('reason') or '-'} | gate={gate}")
    await send_chunked(ctx, "\n".join(lines))


@bot.command(name="토론승인")
async def debate_approve_cmd(ctx, event_id: str):
    eid = event_id.strip().upper()
    ok = db_manager.set_debate_queue_status(eid, "pending", note=f"discord approved by {ctx.author}")
    if not ok:
        await ctx.send(f"⚠️ 자동 토론 승인 실패: `{eid}`")
        return
    await ctx.send(f"✅ 자동 토론 승인 완료: `{eid}`. 다음 `run_debates.bat` 실행 시 처리됩니다.")


@bot.command(name="토론보류")
async def debate_hold_cmd(ctx, event_id: str):
    eid = event_id.strip().upper()
    ok = db_manager.set_debate_queue_status(eid, "held", note=f"discord held by {ctx.author}")
    if not ok:
        await ctx.send(f"⚠️ 자동 토론 보류 실패: `{eid}`")
        return
    await ctx.send(f"⏸️ 자동 토론 보류 완료: `{eid}`")


@bot.command(name="토론기록")
async def debate_history_cmd(ctx):
    rows = db_manager.list_recent_debates(limit=10)
    if not rows:
        await ctx.send("📭 저장된 토론 기록이 없습니다.")
        return
    lines = ["📚 **[최근 토론 기록]**"]
    for row in rows:
        topic = str(row.get("topic") or "").replace("\n", " ").strip()
        if len(topic) > 140:
            topic = topic[:137] + "..."
        lines.append(
            f"- `#{row['id']}` | {row.get('date') or '-'} | {row.get('consensus_status') or '-'}"
        )
        quality = db_manager.get_debate_quality_score(int(row["id"]))
        if quality:
            lines[-1] += f" | quality={quality.get('status')} {quality.get('total_score'):.1f}"
        lines.append(f"  {topic}")
    lines.append("\n`!토론로그 <id>`로 저장된 전체 로그 일부를 확인할 수 있습니다.")
    await send_chunked(ctx, "\n".join(lines))


@bot.command(name="토론로그")
async def debate_log_cmd(ctx, debate_id: int):
    row = db_manager.get_debate(int(debate_id))
    if not row:
        await ctx.send(f"⚠️ 토론 기록을 찾지 못했습니다: `#{debate_id}`")
        return
    max_chars = max(2000, int(os.getenv("DISCORD_DEBATE_LOG_CHARS", "12000")))
    full_log = str(row.get("full_log") or "")
    clipped = full_log[-max_chars:]
    header = (
        f"📜 **[토론 로그 #{row['id']}]**\n"
        f"- date: {row.get('date') or '-'}\n"
        f"- status: {row.get('consensus_status') or '-'}\n"
        f"- topic: {str(row.get('topic') or '').replace(chr(10), ' ')[:500]}\n"
        f"- 표시 범위: 마지막 {len(clipped)}자 / 전체 {len(full_log)}자\n\n"
    )
    quality = db_manager.get_debate_quality_score(int(debate_id))
    if quality:
        detail = quality.get("detail_json", {}) or {}
        header += (
            f"- quality: {quality.get('status')} score={quality.get('total_score'):.1f}\n"
            f"- missing: {', '.join(detail.get('missing', [])) or '-'}\n\n"
        )
    await send_chunked(ctx, header + "```text\n" + clipped[-max_chars:] + "\n```")


@bot.command(name="토론품질")
async def debate_quality_cmd(ctx, debate_id: int):
    quality = db_manager.get_debate_quality_score(int(debate_id))
    if not quality:
        await ctx.send(f"⚠️ 토론 품질 점수가 없습니다: `#{debate_id}`")
        return
    detail = quality.get("detail_json", {}) or {}
    checks = detail.get("checks", {}) or {}
    lines = [
        f"🧪 **[토론 품질 점수 #{debate_id}]**",
        f"- status: {quality.get('status')} | score={quality.get('total_score'):.1f}",
        f"- event_id: {quality.get('event_id') or '-'} | scored_at={quality.get('scored_at')}",
    ]
    for key, passed in checks.items():
        lines.append(f"- {key}: {'PASS' if passed else 'MISS'}")
    await send_chunked(ctx, "\n".join(lines))


@bot.command(name="성과")
async def performance_cmd(ctx):
    summary = performance_tracker.summarize(limit=500)
    rows = db_manager.list_signal_performance(limit=8)
    lines = [
        "📈 **[시그널 성과 요약]**",
        f"- count: {summary.get('count', 0)}",
        f"- win_rate: {summary.get('win_rate', 0.0) * 100:.1f}%",
        f"- avg_return: {summary.get('avg_return_pct', 0.0):+.2f}%",
        f"- avg_alpha: {summary.get('avg_alpha_pct', 0.0):+.2f}%",
        f"- expectancy: {summary.get('expectancy_pct', 0.0):+.2f}%",
    ]
    if rows:
        lines.append("\n최근 측정:")
        for row in rows:
            lines.append(
                f"- `{row['event_id']}` {row['ticker']} {row['horizon']} "
                f"ret={row['return_pct']:+.2f}% alpha={row['alpha_pct']:+.2f}%"
            )
    await send_chunked(ctx, "\n".join(lines))


@bot.command(name="변화트리거")
async def review_trigger_cmd(ctx):
    rows = db_manager.list_investment_review_triggers(limit=15, statuses=["open"])
    if not rows:
        await ctx.send("📭 현재 열린 투자 변화 트리거가 없습니다.")
        return
    lines = ["📎 **[투자 변화 트리거]**"]
    for row in rows:
        lines.append(
            f"- `#{row['id']}` | `{row['event_id']}` | {row['ticker'] or '-'} {row['trigger_type']} | "
            f"priority={row['priority']} | status={row['status']}"
        )
        lines.append(f"  {row['summary']}")
    await send_chunked(ctx, "\n".join(lines))


@bot.command(name="승인")
async def approve_cmd(ctx, event_id: str):
    """
    승인 대기 이벤트를 승인하고 즉시 페이퍼 체결합니다.
    """
    eid = event_id.strip().upper()
    ok = db_manager.approve_request(eid, approved_by=str(ctx.author), note="discord manual approve")
    if not ok:
        await ctx.send(f"⚠️ 승인 실패: `{eid}` (대기 상태가 아니거나 이벤트 없음)")
        return
    done, msg = trading_executor.execute_paper(eid, approved_by=str(ctx.author))
    await ctx.send(msg if done else f"승인은 되었지만 실행 실패: {msg}")


@bot.command(name="거부")
async def reject_cmd(ctx, event_id: str):
    """
    승인 대기 이벤트를 거부합니다.
    """
    eid = event_id.strip().upper()
    ok = db_manager.reject_request(eid, rejected_by=str(ctx.author), note="discord manual reject")
    if not ok:
        await ctx.send(f"⚠️ 거부 실패: `{eid}` (대기 상태가 아니거나 이벤트 없음)")
        return
    db_manager.set_recommendations_status(eid, "rejected")
    db_manager.set_signal_event_status(eid, "rejected")
    await ctx.send(f"🛑 거부 완료: `{eid}`")


@bot.command(name="자동매매중지")
async def kill_switch_on_cmd(ctx):
    db_manager.set_kill_switch(True)
    await ctx.send("🧯 자동매매 중지(kill_switch=ON)로 변경했습니다.")


@bot.command(name="자동매매재개")
async def kill_switch_off_cmd(ctx):
    db_manager.set_kill_switch(False)
    state = db_manager.get_guardrail_state()
    await ctx.send(
        "✅ 자동매매 재개(kill_switch=OFF).\n"
        f"- daily_limit={state.get('daily_order_limit')}, hourly_limit={state.get('hourly_order_limit')}, "
        f"daily_loss_limit={state.get('daily_loss_limit')}"
    )


@bot.command(name="가드레일")
async def guardrail_cmd(ctx):
    state = db_manager.get_guardrail_state()
    await ctx.send(
        "🛡️ **[리스크 가드레일 상태]**\n"
        f"- kill_switch: {'ON' if state.get('kill_switch') else 'OFF'}\n"
        f"- daily_order_limit: {state.get('daily_order_limit')}\n"
        f"- hourly_order_limit: {state.get('hourly_order_limit')}\n"
        f"- daily_loss_limit: {state.get('daily_loss_limit')}\n"
        f"- updated_at: {state.get('updated_at')}"
    )

@bot.command()
async def 토론(ctx, *, user_query: str):
    """
    다중 AI 모델이 순차적으로 발언하고 로컬 팩트체커가 개입하는 완전체 토론 명령어
    사용 예시: !토론 워렌 버핏이 최근 산 전기차 주식이 뭐야?
    """
    debate_controller = DebateController(llm_manager, fact_checker, crawler)
    raw_portfolio = portfolio_manager.load_raw_portfolio()
    if raw_portfolio:
        holdings, _warnings = portfolio_manager.parse_holdings(raw_portfolio)
        holdings_agg = portfolio_manager.aggregate_holdings(holdings)
        auto_context = portfolio_manager.build_llm_context(holdings_agg, raw_text=raw_portfolio)
        channel_portfolio_context[ctx.channel.id] = auto_context

    portfolio_context = channel_portfolio_context.get(ctx.channel.id, "")
    
    # 2~3라운드의 거대한 토론 및 팩트체크 로직을 비동기로 실행
    final_history, debate_id = await debate_controller.run_full_debate(
        ctx, user_query, portfolio_context=portfolio_context
    )
    quality = score_debate_quality(final_history, debate_id=int(debate_id))
    db_manager.save_debate_quality_score(quality)
    
    # ==========================
    # 결론 보류 및 저장 안내
    # ==========================
    await ctx.send(
        "💡 **[결론 및 임시 저장]**\n"
        "현재 라운드의 모든 발언(GPT, Claude, Gemini)과 로컬 gpt-oss-20b의 교차 검증 결과 등 토론 전체의 맥락이 봇의 단기 기억 공간에 저장되었습니다.\n"
        f"토론 품질 점수: {quality['status']} ({quality['total_score']:.1f}/100)\n"
        "*(이제부터 !명령어 없이 일반 채팅을 치시면, AI가 이 문맥을 기억한 상태로 대화를 이어나갑니다!)*"
    )
    
    # 🌟 핵심: !토론 을 칠 때마다 기존 기억(history)은 덮어씌워지고 백지에서 '새로운 주제'로 시작됩니다.
    channel_memory[ctx.channel.id] = {
        "history": final_history[-MAX_CHANNEL_HISTORY_CHARS:],
        "db_id": debate_id
    }

@bot.event
async def on_message(message):
    # 봇 자신이 쓴 메시지는 무시
    if message.author.bot:
        return
        
    # ! 로 시작하는 명령어는 원래대로 처리하도록 넘김
    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    # 일반 채팅일 경우, 기존 토론 문맥이 살아있다면 이어서 대답함
    if message.channel.id in channel_memory:
        mem = channel_memory[message.channel.id]
        user_text = message.content
        await message.channel.send("💬 `문맥을 파악 중... gpt-5.2-2025-12-11이 추가 의견에 대해 답변합니다 (DB 저장 중).`")
        
        # 추가 개입 기록
        added_log = f"\n[사용자 '{message.author.name}'의 추가 개입]: {user_text}\n"
        mem["history"] += added_log
        
        sys_prompt = (
            "너는 이 주식 토론방의 대표 AI(GPT)야. 지금까지 진행된 3명의 릴레이 토론 내역과 팩트체크를 모두 기억한 상태에서, "
            "방금 사용자가 던진 '추가 의견'이나 '질문'에 대해 3문장 이내로 명확히 답변하거나 반박해.\n"
            "[출력 형식 제한 사항]\n"
            "- LaTeX 기호($, $$) 사용 금지, 표 사용 금지.\n"
            "- 문단 나누기, 굵은 글씨, 글머리 기호만 사용."
        )
        
        # 컨텍스트를 모두 포함하여 GPT에게 답변 요청
        reply = await llm_manager.get_gpt_response(sys_prompt, mem["history"])
        if is_model_error_text(reply):
            await message.channel.send("⚠️ GPT 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요.")
            return
        await message.channel.send(f"**[gpt-5.2-2025-12-11 응답 (이전 Context 유지)]**\n{reply}")
        
        # AI 다음 기억을 위해 자신이 한 대답도 추가
        reply_log = f"gpt-5.2-2025-12-11 응답: {reply}\n"
        mem["history"] += reply_log
        if len(mem["history"]) > MAX_CHANNEL_HISTORY_CHARS:
            mem["history"] = mem["history"][-MAX_CHANNEL_HISTORY_CHARS:]
        
        # 🔥 영구 저장: 방금 추가된 대화(사용자 질문 + GPT 응답)를 기존 SQLite 회의록 맨 아래에 이어 붙임!
        db_manager.update_debate_log(mem["db_id"], added_log + reply_log)

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token == "your_discord_bot_token_here" or not token:
        print("디스코드 봇 토큰이 .env 파일에 설정되지 않았습니다.")
    else:
        # discord.py 의존성 문제로 경고가 뜰수도 있으나 2026 안정화 버전 사용
        bot.run(token)
