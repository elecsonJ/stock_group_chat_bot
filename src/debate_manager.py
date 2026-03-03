import asyncio
import json
import re
import db_manager
from data_fetcher.pipeline import MasterDataPipeline
from rag_agent import RAGAgent
from json_utils import (
    parse_json_object,
    validate_final_verdict_payload,
    validate_unanimity_payload,
)
from ontology import OntologyStore, HybridResearchPlanner, EvidenceRelationMiner

async def send_chunked(ctx, text: str):
    """디스코드 2000자 제한을 우회하기 위해 메시지를 잘라서 전송합니다."""
    text = str(text)
    if not isinstance(text, str):
        return
    if isinstance(text, str) and len(text) <= 1900:
        await ctx.send(text)
        return
    for i in range(0, len(text), 1900):
        await ctx.send(text[i:i+1900])

class DebateController:
    def __init__(self, llm_manager, fact_checker, crawler):
        self.llm = llm_manager
        self.checker = fact_checker
        self.crawler = crawler
        self.db = db_manager.DBManager()
        self.data_pipeline = MasterDataPipeline(self.llm)
        self.rag_agent = RAGAgent(self.llm)
        self.ontology = OntologyStore()
        self.planner = HybridResearchPlanner(self.ontology)
        self.relation_miner = EvidenceRelationMiner(self.ontology)
        
    async def get_or_fetch_daily_news(self, keyword: str):
        # 1. DB에 오늘자 뉴스가 있는지 확인
        cached_news = self.db.get_daily_news(keyword)
        if cached_news:
            return cached_news, True
            
        # 2. 없으면 새로 크롤링하고 DB에 저장
        news = self.crawler.get_news_rss(keyword)
        if news:
            self.db.save_daily_news(keyword, news)
        return news, False

    async def run_full_debate(self, ctx, user_query: str, portfolio_context: str = ""):
        """메인 봇에서 호출되는 2~3라운드 토론 전체 로직"""
        import os
        import glob
        import json
        
        await ctx.send(f"🔍 `{user_query}` 주제 분석 및 방향성 설정 중...")
        history = f"**[주제]** {user_query}\n\n"
        if portfolio_context.strip():
            history += f"**[사용자 포트폴리오 컨텍스트]**\n{portfolio_context.strip()}\n\n"
            await ctx.send("📦 **[포트폴리오 컨텍스트 주입]** 이번 토론은 사용자 보유 포지션을 함께 고려합니다.")

        # 로컬 모델 가용성 사전 점검 (장애 시 강등 모드)
        local_available = True
        try:
            await self.llm.get_local_response("JSON 출력기", "{\"ping\":\"ok\"}")
        except Exception as e:
            local_available = False
            await ctx.send(f"⚠️ **[강등 모드]** 로컬 모델 연결 실패로 일부 기능이 제한됩니다: {e}")

        def _heuristic_extract_targets(query_text: str) -> tuple[list[str], list[str]]:
            # 로컬 모델이 다운된 경우를 위한 보수적 추출기
            ticker_candidates = re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", query_text)
            stop = {"AI", "ETF", "USD", "KRW", "GDP", "CPI", "PER", "ROE", "MACD", "RSI"}
            tickers = [t for t in ticker_candidates if t not in stop][:2]
            searches = [query_text.strip()] if query_text.strip() else []
            if not searches:
                searches = ["미국 증시 주요 리스크"]
            return tickers, searches[:3]
        
        # 0. RAG 기반 과거 기록 조회 및 투입
        if local_available:
            try:
                await ctx.send("🧠 **[기억 안테나 가동]** 이전 유사 토론 기록 및 요약본에서 과거의 인사이트를 불러옵니다...")
                rag_context = await self.rag_agent.answer_question(user_query)
                if "일치하는 과거 토론이나 요약 데이터가 없습니다" not in rag_context:
                    history += f"**[RAG 과거 토론 맥락 사전지식]**\n{rag_context}\n\n"
                    await ctx.send("✅ 과거 토론 요약 데이터 합류 완료! 현재 토론의 베이스 논거로 투입됩니다.")
                else:
                    await ctx.send("ℹ️ 과거 일치하는 기록이 없어, 최초 주제로 분류하고 토론을 개시합니다.")
            except Exception as e:
                print(f"[RAG] 토론 중 기억 연동 오류: {e}")
        else:
            await ctx.send("ℹ️ 로컬 모델 비활성 상태라 RAG 과거 맥락 주입을 건너뜁니다.")
            
        # 1. 쿼리 성격 분류 (로컬 모델 활용)
        classify_prompt = (
            "다음 사용자의 토론 주제가 '최신 기술/시장 이슈, 기업 동향, 미래 전망' 등 최신 배경지식이 필수적인 주제인지, "
            "아니면 '자산 배분 이론, 일반적인 투자 철학, 단순 원론' 등 보편적 이론에 가까운지 판단해.\n"
            "최신 뉴스/전망이 필요하면 {\"is_recent_issue\": true} 를, 필요하지 않으면 {\"is_recent_issue\": false} 를 JSON 형식으로 출력해."
        )
        
        if local_available:
            try:
                class_json_str = await self.llm.get_local_response(classify_prompt, user_query)
                is_recent_issue = True
                parsed = parse_json_object(class_json_str) or {}
                is_recent_issue = bool(parsed.get("is_recent_issue", True))
            except Exception:
                is_recent_issue = True
        else:
            is_recent_issue = True
            
        if is_recent_issue:
            await ctx.send("📰 **[최신 이슈 기반 토론]** 판정. 로컬 판사가 최근 수일 간의 뉴욕타임스 기사를 정독하고 관련된 지식만 추출합니다...")
            news_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "news_archive")
            files = glob.glob(os.path.join(news_dir, "premium_news_*.txt"))
            if files:
                files.sort(reverse=True)  # 가장 최근 파일 선택
                recent_news_texts = []
                # 최근 5일치 뉴스를 취합 (토큰 폭발을 고려해 방대한 양)
                for f_path in files[:5]:
                    try:
                        with open(f_path, 'r', encoding='utf-8') as f:
                            recent_news_texts.append(f.read())
                    except Exception:
                        pass
                        
                combined_news = "\n".join(recent_news_texts)
                
                # 로컬 모델(gpt-oss-20b)에게 필요한 뉴스만 선별/요약 부탁
                filter_prompt = (
                    f"너는 최고 수준의 금융/IT 정보 분석가야. 아래에는 최근 수일 간의 방대한 '뉴욕타임스 글로벌 프리미엄 뉴스 요약본' 텍스트 뭉치가 있어.\n"
                    f"사용자의 토론 주제는 ['{user_query}'] 야.\n"
                    f"만약 주제가 매우 구체적이라면 그와 직접적으로 연관된 기사들만 골라서 깊이 있게 요약해주고, "
                    f"주제가 '투자할 만한 곳', '최근 유망한 이슈' 등 광범위하다면, 제공된 텍스트 전체에서 가장 중요한 호재/악재를 추출해줘.\n"
                    f"각각의 뉴스 항목에 대해 핵심 내용과 구체적인 컨텍스트를 3~4문장 분량으로 상세하게 풀어서 설명해.\n"
                    f"[최종 규칙: 엄격한 팩트 준수]: 절대로 네가 사전 학습한 과거의 주가, 재무제표(PER, ROE 등), 배당률, 수치 등 근거 없는 환각(Hallucination) 데이터를 섞어 넣지 마라. 오직 주어진 텍스트 파일(뉴스본)에 있는 숫자만 활용하고 없으면 적지 마라."
                )
                if local_available:
                    try:
                        # 로컬 모델 글자수 한계 방지를 위해 최대 5만자까지만 앞부분에서 잘라 넘김
                        filtered_news = await self.llm.get_local_response(filter_prompt, combined_news[:50000])
                        history += f"**[최근 글로벌 프리미엄 주요 뉴스 중 주제 관련 핵심 필터링 요약]**\n{filtered_news}\n\n"
                        await ctx.send("✅ 방대한 뉴스 DB에서 이번 토론 주제에 꼭 필요한 '핵심 지식'만 깔끔하게 정제 완료했습니다!")
                    except Exception as e:
                        await ctx.send(f"⚠️ 지능형 뉴스 필터링 오류: {e} (최신 1일치 원문으로 대체합니다.)")
                        if recent_news_texts:
                            news_excerpt = recent_news_texts[0]
                            news_excerpt_str = news_excerpt if isinstance(news_excerpt, str) else str(news_excerpt)
                            history += f"**[오늘의 글로벌 뉴스 (보조 데이터)]**\n{news_excerpt_str[:3000]}\n\n"
                        else:
                            history += "**[오늘의 글로벌 뉴스 (보조 데이터)]**\n(수집된 글로벌 뉴스 원문 텍스트가 없습니다.)\n\n"
                else:
                    if recent_news_texts:
                        news_excerpt = recent_news_texts[0]
                        news_excerpt_str = news_excerpt if isinstance(news_excerpt, str) else str(news_excerpt)
                        history += f"**[오늘의 글로벌 뉴스 (보조 데이터)]**\n{news_excerpt_str[:3000]}\n\n"
                    await ctx.send("ℹ️ 로컬 모델 비활성 상태라 뉴스 정제 요약은 건너뛰고 원문 일부를 사용합니다.")
            else:
                await ctx.send("⚠️ 수집된 프리미엄 뉴스 백업 파일이 없습니다.")
        else:
            await ctx.send("💡 **[일반 투자 철학/원론적 토론 모드]** (최신 뉴스보다 보편적인 투자 논리에 집중합니다.)")

        # 온톨로지 기반 사전 플래닝 (온톨로지 -> RAG -> 웹검색 동선)
        ontology_plan = self.planner.build_plan(user_query)
        history += (
            "**[온톨로지 리서치 플랜]**\n"
            f"{json.dumps(ontology_plan, ensure_ascii=False, indent=2)}\n\n"
        )
        linked_preview = ", ".join(
            [e.get("ticker") or e.get("name") or "" for e in ontology_plan.get("linked_entities", [])[:4]]
        )
        await ctx.send(
            f"🧭 **[리서치 플랜]** mode={ontology_plan.get('mode')} | coverage={ontology_plan.get('coverage')} | linked={linked_preview or '없음'}"
        )
        
        roles = {
            "gpt-5.2-2025-12-11": "찬성 또는 공격적 성장 모델 지지",
            "claude-sonnet-4-6": "안전/가치 투자 및 반대/리스크 부각",
            "gemini-3-flash-preview": "제3의 시각 및 논리적 오류 저격"
        }
        final_model_names = tuple(roles.keys())
        
        # ==========================================
        # Phase 1: 자동 리서치 국면 (Fact-Sheet 구축)
        # ==========================================
        await ctx.send("🔍 **[Phase 1: 자동 리서치 국면 - 주제 분석 및 팩트 시트 구축 중...]**")
        
        extract_prompt = (
            "다음 토론 주제를 읽고, 팩트체크를 위해 필요한 주식 티커(최대 2개)와 여러 관점의 웹 검색어(최대 3개)를 JSON으로 추출해.\n"
            "- 미국 주식은 그대로 티커를 써 (예: AAPL, TSLA).\n"
            "- 한국 주식(코스피)은 6자리 숫자 뒤에 '.KS'를 붙여 (예: 삼성전자 -> 005930.KS).\n"
            "- 한국 주식(코스닥)은 6자리 숫자 뒤에 '.KQ'를 붙여 (예: 에코프로 -> 086520.KQ).\n"
            "출력 포맷: 정확히 아래 예시와 동일하게 JSON만 출력해.\n"
            "예: {\"tickers\": [\"TSLA\", \"005930.KS\"], \"searches\": [\"테슬라 2024 실적 전망\", \"삼성전자 HBM 공급 현황\", \"글로벌 전기차 수요 변화\"]}"
        )
        
        fact_sheet = ""
        try:
            planner_tickers = ontology_plan.get("tickers", []) if isinstance(ontology_plan, dict) else []
            planner_searches = ontology_plan.get("web_queries", []) if isinstance(ontology_plan, dict) else []
            query_upper_tokens = set(re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", user_query.upper()))

            if local_available:
                extract_json_str = await self.llm.get_local_response(extract_prompt, user_query)
                extract_data = parse_json_object(extract_json_str) or {}
                tickers = extract_data.get("tickers", []) if extract_data else []
                searches = extract_data.get("searches", []) if extract_data else []
                if extract_data and "search" in extract_data and isinstance(extract_data["search"], str):
                    searches.append(extract_data["search"])
            else:
                tickers, searches = _heuristic_extract_targets(user_query)

            # 온톨로지 플랜 기반 결과 병합 (중복 제거)
            merged_tickers = []
            for t in [*planner_tickers, *tickers]:
                ts = str(t).strip()
                if ts and ts not in merged_tickers:
                    merged_tickers.append(ts)
            # 쿼리 문맥과 무관한 환각 티커 제거: (1) 사용자 질문에 있거나 (2) 온톨로지 링크된 티커만 허용
            linked_tickers = {
                str(e.get("ticker", "")).strip().upper()
                for e in (ontology_plan.get("linked_entities", []) if isinstance(ontology_plan, dict) else [])
                if isinstance(e, dict)
            }
            filtered_tickers = []
            for t in merged_tickers:
                tu = t.upper()
                if tu in query_upper_tokens or tu in linked_tickers:
                    filtered_tickers.append(t)
            tickers = filtered_tickers[:2]

            merged_searches = []
            seen_searches = set()
            banned_search_fragments = (
                "stock ticker company profile",
                "검색할 구체적인 키워드",
                "반대 관점 키워드",
            )
            for q in [*planner_searches, *searches]:
                qs = str(q).strip()
                if not qs:
                    continue
                if any(b in qs.lower() for b in banned_search_fragments):
                    continue
                qn = re.sub(r"\s+", " ", qs.lower())
                if qs and qn not in seen_searches:
                    merged_searches.append(qs)
                    seen_searches.add(qn)
            searches = merged_searches[:4]

            if tickers or searches:
                await ctx.send(f"📊 **[초고밀도 파이프라인 가동]** 티커: {tickers}, 다중 검색: {searches}")
                fact_sheet = await self.data_pipeline.build_ultimate_fact_sheet(tickers, searches)
        except Exception as e:
            await ctx.send(f"⚠️ 리서치 추출 중 오류: {e}")
            
        if not fact_sheet.strip():
            fact_sheet = "현재 주제에 대해 별도로 검색된 숫자 및 팩트 데이터가 없습니다. 이미 알고 있는 지식을 활용하세요."
            
        history += f"**[공통 Fact-Sheet]**\n{fact_sheet}\n\n"
        await send_chunked(ctx, f"📄 **[공통 Fact-Sheet 지급 완료]**\n(해당 팩트를 기반으로 토론이 전개됩니다.)\n")

        # 세션 전체에서 이미 수행한 리서치 쿼리/근거ID 추적
        session_searched_queries: set[str] = set()
        session_evidence_ids: set[str] = set()
        speed_mode = os.getenv("DEBATE_SPEED_MODE", "off").strip().lower()
        research_cache_ttl_hours = max(0, int(os.getenv("RESEARCH_CACHE_TTL_HOURS", "12")))
        global_evidence_seq = 0

        methods = {
            "gpt-5.2-2025-12-11": self.llm.get_gpt_response,
            "claude-sonnet-4-6": self.llm.get_claude_response,
            "gemini-3-flash-preview": self.llm.get_gemini_response
        }

        def _sanitize_model_output(text: str) -> str:
            raw = str(text or "")
            cleaned = re.sub(r"<thought>.*?</thought>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()

            # 일부 모델이 생산 공정/메타 로그를 앞에 붙이는 경우, 마지막 구분선 이후 본문만 사용
            if "────────────────" in cleaned:
                parts = cleaned.split("────────────────")
                cleaned = (parts[-1] or "").strip()

            meta_markers = (
                "생산 공정", "분석 및 타겟", "사고과정", "지시사항 점검", "최종 다듬기",
                "사고과정 제거", "완료.", "시작]", "종료]", "초안 작성", "핵심 논리 전개",
            )
            keep_tag_prefixes = ("[SEARCH:", "[최종 선택:", "[근거ID:", "[ACK", "[조준:")
            out_lines = []
            for line in cleaned.splitlines():
                s = line.strip()
                if not s:
                    continue
                if any(s.startswith(prefix) for prefix in keep_tag_prefixes):
                    out_lines.append(line)
                    continue
                if s.startswith("[") and s.endswith("]"):
                    low = s.lower()
                    if any(m in low for m in meta_markers):
                        continue
                out_lines.append(line)
            return "\n".join(out_lines).strip()

        def _extract_search_queries(text: str) -> list[str]:
            queries = re.findall(r"\[SEARCH:\s*(.+?)\]", text or "", flags=re.IGNORECASE | re.DOTALL)
            out = []
            seen = set()
            banned_fragments = (
                "검색할 구체적인 키워드",
                "반대 관점 키워드",
                "키워드",
                "...",
            )
            for q in queries:
                qs = re.sub(r"\s+", " ", q.strip())
                # 모델이 프롬프트 문구를 그대로 복붙하는 경우 차단
                if not qs or any(b in qs for b in banned_fragments):
                    continue
                if len(qs) < 6:
                    continue
                qn = qs.lower()
                if qs and qn not in seen:
                    out.append(qs)
                    seen.add(qn)
            return out[:2]

        def _has_ack(text: str) -> bool:
            return bool(re.search(r"\[ACK(?:[^\]]*)\]", text or "", flags=re.IGNORECASE))

        def _extract_target_model(text: str) -> str | None:
            m = re.search(r"\[조준:\s*([^\]]+)\]", text or "", flags=re.IGNORECASE)
            if not m:
                return None
            raw = m.group(1).strip().lower()
            if any(k in raw for k in ("gpt", "openai", "chatgpt")):
                return "gpt-5.2-2025-12-11"
            if "claude" in raw:
                return "claude-sonnet-4-6"
            if "gemini" in raw:
                return "gemini-3-flash-preview"
            return None

        def _register_evidence_ids(query: str, package: dict) -> None:
            nonlocal global_evidence_seq
            if not isinstance(package, dict):
                return
            evidences = package.get("evidences", [])
            if not isinstance(evidences, list):
                return
            for ev in evidences:
                if not isinstance(ev, dict):
                    continue
                global_evidence_seq += 1
                gid = f"EV{global_evidence_seq:04d}"
                ev["global_evidence_id"] = gid
                ev["query"] = query
                session_evidence_ids.add(gid)

        async def run_phase2_debate(current_history: str, turn_idx: int) -> tuple[str, int, dict]:
            gpt_name = "gpt-5.2-2025-12-11"
            claude_name = "claude-sonnet-4-6"
            gemini_name = "gemini-3-flash-preview"
            loop_meta: dict[str, object] = {
                "ack_models": [],
                "target_events": [],
                "speed_mode_used": False,
            }

            if turn_idx == 1:
                # Loop 1: 완전 블라인드(독립적) 병렬 생성
                await ctx.send(f"🔥 **[Phase 2 (Loop {turn_idx}): 블라인드 동시 논증 (각자 독립적 관점)]**")
                dyn_hist = current_history
                base_inst = (
                    "너는 세계 최고 수준의 월스트리트 헤지펀드 매니저야. "
                    "제공된 Fact-Sheet를 바탕으로 철저하고 뾰족하게 논증해. "
                    "다른 관점에 휘둘리지 않고 오직 너의 스탠스를 3~4문장으로 강력히 주장해. "
                    "내부 사고과정은 노출하지 말고, 근거가 부족한 항목은 단정하지 마."
                )
                
                async def fetch_blind(model_name: str) -> tuple[str, str]:
                    try:
                        prompt = f"{base_inst} 너의 스탠스는 '{roles[model_name]}'이야."
                        reply = await methods[model_name](prompt, dyn_hist)
                        return model_name, str(reply)
                    except Exception as e:
                        return model_name, f"<thought>에러 발생</thought> {e}"
                
                blind_models = [gpt_name, claude_name, gemini_name]
                if speed_mode == "first_completed":
                    await ctx.send("⚡ **[속도전 모드]** Loop 1은 FIRST_COMPLETED 결과 1개만 채택합니다.")
                    loop_meta["speed_mode_used"] = True
                    task_map = {
                        asyncio.create_task(fetch_blind(model_name)): model_name for model_name in blind_models
                    }
                    done, pending = await asyncio.wait(
                        list(task_map.keys()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    first_task = next(iter(done))
                    first_result = await first_task
                    for p in pending:
                        p.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    results = [first_result]
                else:
                    tasks = [fetch_blind(m) for m in blind_models]
                    # 동시에 의견 도출 (서로의 의견을 모름)
                    results = await asyncio.gather(*tasks)

                for model_n, rep_text in results:
                    # 내부 사고(<thought>)는 디스코드 UI에만 송출하고 공통 히스토리에서는 제거
                    clean_rep = re.sub(r'<thought>.*?</thought>', '', str(rep_text), flags=re.DOTALL).strip()
                    clean_rep = _sanitize_model_output(clean_rep)
                    dyn_hist += f"[{model_n} {turn_idx}R 최초 발언]:\n{clean_rep}\n\n"
                    display_text = clean_rep if clean_rep else str(rep_text)
                    await send_chunked(ctx, f"🗣️ **[{model_n} 블라인드 발언]**\n{display_text}")
                    if _has_ack(str(rep_text)):
                        loop_meta["ack_models"] = [*loop_meta.get("ack_models", []), model_n]

                return dyn_hist, 0, loop_meta

            else:
                # Loop 2부터: 순차적 크로스 반박 (서로의 의견을 보고 타격)
                await ctx.send(f"🔥 **[Phase 2 (Loop {turn_idx}): 이전 라운드를 병합한 순차적 릴레이 반박]**")
                dyn_hist = current_history
                base_inst = (
                    "너는 세계 최고 수준의 월스트리트 헤지펀드 매니저야. 이미 앞선 라운드들에서 다른 매니저들의 주장과 논란이 나왔어. "
                    "상대방 주장의 맹점을 철저히 공격하며 너의 논리를 강화해. 주어진 팩트체크 데이터를 인용해. "
                    "만약 확실한 판단을 위해 직접 확인해야 할 기사의 원문이나 인터넷 웹 검색이 필요하다면 답변 마지막 줄에 '[SEARCH: 검색할 구체적인 키워드]' 라고 적어둬. "
                    "3~4문장으로 작성하고, 내부 사고과정은 노출하지 마.\n"
                    "[치명적 규칙 (Anti-Anchoring)]: 토론 중 상대방이나 자신이 [SEARCH]를 통해 실시간 검색 팩트(예: 실적 하향, 새로운 수치)를 가져와 너의 기존 논거나 전제가 깨졌다면, 억지를 부리지 말고 유연하게 수용해라.\n"
                    "[추가 강력 지시 (맞불 리서치)]: 상대가 SEARCH 근거를 제시하면, 그 근거를 검증/반박할 구체 쿼리를 직접 작성해 [SEARCH: ...]로 제시하라. "
                    "문자 그대로 '검색할 구체적인 키워드' 또는 '반대 관점 키워드' 같은 플레이스홀더 문구는 절대 쓰지 마라."
                )
                
                # 보조 함수: 요청에 [SEARCH: xxx] 가 있는지 확인하고 리서치 수행
                async def handle_research_request(model_n: str, text: str, hist: str) -> tuple[str, int]:
                    queries = _extract_search_queries(text)
                    if not queries:
                        return hist, 0
                    research_count = 0
                    for query in queries:
                        normalized_query = re.sub(r"\s+", " ", query.lower())
                        # 이미 이번 토론에서 검색된 키워드와 비슷하다면 건너뛰어 토큰 폭발 방지
                        if normalized_query in session_searched_queries:
                            await ctx.send(f"🕵️‍♂️ **[심층 리서치 보류]** '{query}'는 이미 검색된 맥락입니다. 기존 데이터를 재활용합니다.")
                            continue

                        session_searched_queries.add(normalized_query)
                        await ctx.send(f"🕵️‍♂️ **[심층 리서치 발동]** {model_n}의 요청으로 웹 원문 검색 중... 🔍 `{query}`")

                        package = None
                        try:
                            if research_cache_ttl_hours > 0:
                                cached = self.db.get_cached_research_evidence(query, max_age_hours=research_cache_ttl_hours)
                                if isinstance(cached, dict):
                                    package = cached
                                    await ctx.send(
                                        f"⚡ **[리서치 캐시 히트]** `{query}` "
                                        f"(TTL {research_cache_ttl_hours}h 내 기존 근거 재사용)"
                                    )

                            if not package:
                                # 구조화된 증거 패키지를 우선 생성 및 저장
                                package = await self.checker.run_deep_research_package(query)
                                self.db.save_research_evidence(user_query, query, package)

                            _register_evidence_ids(query, package)
                            try:
                                rel_result = self.relation_miner.ingest_evidence_package(user_query, query, package)
                                if rel_result.get("added_relations", 0):
                                    await ctx.send(
                                        f"🧩 **[온톨로지 자동 갱신]** relation +{rel_result.get('added_relations')} "
                                        f"(query: `{query}`)"
                                    )
                            except Exception:
                                pass
                        except Exception as e:
                            package = {
                                "query": query,
                                "status": "error",
                                "evidences": [],
                                "limitations": [f"심층 리서치 실패: {e}"],
                                "summary": f"심층 리서치 실패: {e}",
                            }

                        evidences = package.get("evidences", []) if isinstance(package, dict) else []
                        summary = package.get("summary", "") if isinstance(package, dict) else ""
                        source_lines = []
                        for ev in evidences[:5]:
                            ev_id = ev.get("global_evidence_id") or ev.get("evidence_id")
                            source_lines.append(f"- {ev_id}: {ev.get('title')} ({ev.get('domain')})\n  {ev.get('url')}")
                        source_text = "\n".join(source_lines) if source_lines else "- 출처 없음"
                        research_result = (
                            f"[Evidence Package]\n"
                            f"- query: {query}\n"
                            f"- status: {package.get('status') if isinstance(package, dict) else 'unknown'}\n"
                            f"- evidence_count: {len(evidences)}\n"
                            f"- limitations: {package.get('limitations', []) if isinstance(package, dict) else []}\n\n"
                            f"[요약]\n{summary}\n\n"
                            f"[출처 목록]\n{source_text}"
                        )

                        hist += f"\n[시스템 긴급 투입 데이터: '{query}' 웹 리서치 요약]\n{research_result}\n\n"
                        await send_chunked(ctx, f"📜 **[리서치 완료]** '{query}'에 대한 팩트가 토론장에 전송되었습니다.")
                        research_count += 1
                    return hist, research_count

                async def maybe_instant_defense(
                    attacker_model: str,
                    source_reply: str,
                    hist: str,
                    defended_targets: set[str],
                ) -> tuple[str, int]:
                    target_model = _extract_target_model(source_reply)
                    if not target_model or target_model == attacker_model:
                        return hist, 0
                    if target_model in defended_targets:
                        return hist, 0

                    defended_targets.add(target_model)
                    loop_meta["target_events"] = [
                        *loop_meta.get("target_events", []),
                        {"attacker": attacker_model, "target": target_model},
                    ]
                    await ctx.send(
                        f"🎯 **[즉각 방어권 발동]** {attacker_model}의 [조준] 요청으로 {target_model}이(가) 즉시 반론합니다."
                    )
                    defense_prompt = (
                        f"{base_inst} 너의 스탠스는 '{roles[target_model]}'이야. "
                        "상대가 [조준:...] 태그로 직접 공격했다. 핵심 반박만 2~3문장으로 즉각 방어해. "
                        "필요하면 마지막 줄에 [SEARCH: ...]를 포함할 수 있다."
                    )
                    try:
                        defense_rep = await methods[target_model](defense_prompt, hist)
                    except Exception as e:
                        defense_rep = f"오류: {e}"

                    clean_defense = re.sub(r'<thought>.*?</thought>', '', str(defense_rep), flags=re.DOTALL).strip()
                    clean_defense = _sanitize_model_output(clean_defense)
                    hist += f"[{target_model} {turn_idx}R 즉각 방어({attacker_model} 조준)]:\n{clean_defense}\n\n"
                    display_defense = clean_defense if clean_defense else str(defense_rep)
                    await send_chunked(ctx, f"🛡️ **[{target_model} 즉각 방어]**\n{display_defense}")

                    if _has_ack(str(defense_rep)):
                        loop_meta["ack_models"] = [*loop_meta.get("ack_models", []), target_model]

                    hist, defense_research = await handle_research_request(target_model, clean_defense, hist)
                    return hist, defense_research

                # Turn 1: GPT 
                await ctx.send(f"🗣️ **[{gpt_name} 반박 및 논증 갱신 중...]**")
                gpt_prompt = f"{base_inst} 너의 스탠스는 '{roles[gpt_name]}'이야."
                try:
                    gpt_rep = await methods[gpt_name](gpt_prompt, dyn_hist)
                except Exception as e:
                    gpt_rep = f"<thought>에러 발생</thought> {e}"
                
                clean_gpt = re.sub(r'<thought>.*?</thought>', '', str(gpt_rep), flags=re.DOTALL).strip()
                clean_gpt = _sanitize_model_output(clean_gpt)
                dyn_hist += f"[{gpt_name} {turn_idx}R 주장]:\n{clean_gpt}\n\n"
                display_gpt = clean_gpt if clean_gpt else str(gpt_rep)
                await send_chunked(ctx, f"🗣️ **[{gpt_name}]**\n{display_gpt}")
                if _has_ack(str(gpt_rep)):
                    loop_meta["ack_models"] = [*loop_meta.get("ack_models", []), gpt_name]
                dyn_hist, gpt_research = await handle_research_request(gpt_name, clean_gpt, dyn_hist)
                defended_targets: set[str] = set()
                dyn_hist, gpt_def_research = await maybe_instant_defense(gpt_name, clean_gpt, dyn_hist, defended_targets)

                # Turn 2: Claude 
                await ctx.send(f"🗣️ **[{claude_name} 앞선 발언 타격 중...]**")
                claude_prompt = f"{base_inst} 너의 스탠스는 '{roles[claude_name]}'이야."
                try:
                    claude_rep = await methods[claude_name](claude_prompt, dyn_hist)
                except Exception as e:
                    claude_rep = f"<thought>에러 발생</thought> {e}"
                
                clean_claude = re.sub(r'<thought>.*?</thought>', '', str(claude_rep), flags=re.DOTALL).strip()
                clean_claude = _sanitize_model_output(clean_claude)
                dyn_hist += f"[{claude_name} {turn_idx}R 반박]:\n{clean_claude}\n\n"
                display_claude = clean_claude if clean_claude else str(claude_rep)
                await send_chunked(ctx, f"🗣️ **[{claude_name}]**\n{display_claude}")
                if _has_ack(str(claude_rep)):
                    loop_meta["ack_models"] = [*loop_meta.get("ack_models", []), claude_name]
                dyn_hist, claude_research = await handle_research_request(claude_name, clean_claude, dyn_hist)
                dyn_hist, claude_def_research = await maybe_instant_defense(claude_name, clean_claude, dyn_hist, defended_targets)

                # Turn 3: Gemini 
                await ctx.send(f"🗣️ **[{gemini_name} 제3의 시각으로 국면 전환 중...]**")
                gemini_prompt = f"{base_inst} 너의 스탠스는 '{roles[gemini_name]}'이야. 팩트 데이터에 어긋난 억지 주장을 펼친 쪽을 폭격해."
                try:
                    gemini_rep = await methods[gemini_name](gemini_prompt, dyn_hist)
                except Exception as e:
                    gemini_rep = f"<thought>에러 발생</thought> {e}"
                
                clean_gemini = re.sub(r'<thought>.*?</thought>', '', str(gemini_rep), flags=re.DOTALL).strip()
                clean_gemini = _sanitize_model_output(clean_gemini)
                dyn_hist += f"[{gemini_name} {turn_idx}R 타격]:\n{clean_gemini}\n\n"
                display_gemini = clean_gemini if clean_gemini else str(gemini_rep)
                await send_chunked(ctx, f"🗣️ **[{gemini_name}]**\n{display_gemini}")
                if _has_ack(str(gemini_rep)):
                    loop_meta["ack_models"] = [*loop_meta.get("ack_models", []), gemini_name]
                dyn_hist, gemini_research = await handle_research_request(gemini_name, clean_gemini, dyn_hist)
                dyn_hist, gemini_def_research = await maybe_instant_defense(gemini_name, clean_gemini, dyn_hist, defended_targets)

                research_count = (
                    gpt_research + claude_research + gemini_research
                    + gpt_def_research + claude_def_research + gemini_def_research
                )
                return dyn_hist, research_count, loop_meta

        # ==========================================
        # Phase 3: 무자비한 크로스 체크 및 1문장 최종 결론
        # ==========================================
        async def run_phase3_cross_check(current_history: str, turn_idx: int) -> str:
            await ctx.send(f"🔥 **[Phase 3 (Loop {turn_idx}): 크로스 체크 및 최종 변론 (동시 진행)]**")

            evidence_hint = ", ".join(sorted(session_evidence_ids)[:12]) if session_evidence_ids else "없음"
            final_prompt = (
                "지금까지의 모든 쟁점, 상대방의 공격, Fact-Sheet 데이터를 종합하여 최종 변론을 해. "
                "더 이상의 반박이 어렵도록 자신의 스탠스를 방어하는 확고한 1문장을 도출해. "
                "내부 사고과정은 노출하지 말고 결론만 써라. "
                "글의 맨 마지막에 반드시 [최종 선택: (나의 핵심 주장을 1~3단어로 요약)] 키워드를 달아. "
                "(예: [최종 선택: 엔비디아 비중 확대], [최종 선택: 관망 및 현금 확보] 등)\n"
                f"[근거 인용 규칙] 사용 가능한 근거ID: {evidence_hint}\n"
                "마지막 줄에 반드시 [근거ID: EV0001, EV0002] 형식으로 근거ID를 적어라. "
                "근거가 없으면 [근거ID: 없음]을 반드시 표기해."
            )
            
            final_hist = current_history
            
            gpt_name = "gpt-5.2-2025-12-11"
            claude_name = "claude-sonnet-4-6"
            gemini_name = "gemini-3-flash-preview"
            
            async def fetch_final(n: str, model_name: str) -> tuple[str, str]:
                try:
                    reply = await methods[model_name](f"너는 {model_name}야. {final_prompt}", final_hist)
                    return model_name, str(reply)
                except Exception as e:
                    return model_name, f"오류: {e}"

            def _extract_cited_evidence_ids(text: str) -> list[str]:
                match = re.search(r"\[근거ID:\s*([^\]]+)\]", text or "", flags=re.IGNORECASE)
                if not match:
                    return []
                raw = match.group(1)
                ids = re.findall(r"EV\d{4}", raw.upper())
                out = []
                seen = set()
                for ev_id in ids:
                    if ev_id in session_evidence_ids and ev_id not in seen:
                        out.append(ev_id)
                        seen.add(ev_id)
                return out

            async def _repair_evidence_tag(model_name: str, text: str) -> str:
                if not session_evidence_ids:
                    if re.search(r"\[근거ID:\s*[^\]]+\]", text, flags=re.IGNORECASE):
                        return text
                    return f"{text.rstrip()}\n[근거ID: 없음]"

                has_tag = bool(re.search(r"\[근거ID:\s*[^\]]+\]", text, flags=re.IGNORECASE))
                cited = _extract_cited_evidence_ids(text)
                if has_tag and cited:
                    return text

                repair_prompt = (
                    "아래 최종 결론에 근거ID 표기가 누락되었거나 잘못되었습니다. "
                    "문장을 유지하되 마지막 줄에 [근거ID: ...]를 반드시 붙여 단일 답변으로 다시 출력하세요.\n"
                    f"사용 가능한 근거ID 목록: {', '.join(sorted(session_evidence_ids)[:20])}\n"
                    "사용 가능한 ID만 적고, 없으면 [근거ID: 없음]을 적으세요.\n\n"
                    f"[기존 답변]\n{text}"
                )
                try:
                    repaired = await methods[model_name]("근거ID 형식 복구기", repair_prompt)
                    return str(repaired)
                except Exception:
                    return f"{text.rstrip()}\n[근거ID: 없음]"
                    
            tasks = [
                fetch_final("gpt", gpt_name),
                fetch_final("claude", claude_name),
                fetch_final("gemini", gemini_name)
            ]
            
            final_results = await asyncio.gather(*tasks)
            for model_n, rep_text in final_results:
                repaired_text = await _repair_evidence_tag(model_n, str(rep_text))
                clean_rep = re.sub(r'<thought>.*?</thought>', '', str(repaired_text), flags=re.DOTALL).strip()
                clean_rep = _sanitize_model_output(clean_rep)
                final_hist += f"[{model_n} {turn_idx}R 최종 결론]: {clean_rep}\n\n"
                display_text = clean_rep if clean_rep else str(rep_text)
                await send_chunked(ctx, f"🎯 **[{model_n} 최종 결론]**\n{display_text}")
                
            return final_hist

        # ==========================================
        # Phase 4: 로컬 수석 판사(gpt-oss-20b)의 최종 판정 및 루프 컨트롤
        # ==========================================
        def _normalize_choice_label(choice: str) -> str:
            c = (choice or "").strip().lower()
            if not c:
                return "미분류"

            wait_keywords = ("보류", "관망", "대기", "현금", "홀드", "진입 금지", "wait")
            reduce_keywords = ("비중 축소", "축소", "감축", "reduce")
            buy_keywords = ("집중", "매수", "확대", "롱", "비중 확대", "buy")

            if any(k in c for k in wait_keywords):
                return "진입 보류/관망"
            if any(k in c for k in reduce_keywords):
                return "비중 축소"
            if any(k in c for k in buy_keywords):
                return "집중/매수"
            return choice.strip()

        def _extract_latest_model_choice(history_text: str, model_name: str) -> str:
            model_alt = "|".join(re.escape(n) for n in final_model_names)
            pattern = re.compile(
                rf"\[{re.escape(model_name)} \d+R 최종 결론\]:\s*(.*?)(?=\n\[(?:{model_alt}) \d+R 최종 결론\]:|\Z)",
                flags=re.DOTALL
            )
            matches = list(pattern.finditer(history_text))
            if not matches:
                return ""

            latest = matches[-1].group(1).strip()
            tag_match = re.search(r"\[최종 선택:\s*([^\]]+)\]", latest)
            if tag_match:
                return tag_match.group(1).strip()
            return latest[:120].strip()

        async def check_unanimity(current_history: str) -> dict:
            await ctx.send("⚖️ **[로컬 수석 판사 1차 판정: 만장일치 여부 확인 중...]**")
            # 1) 규칙 기반 우선 판정 (ParseError 최소화)
            raw_votes = {
                "GPT": _extract_latest_model_choice(current_history, "gpt-5.2-2025-12-11"),
                "Claude": _extract_latest_model_choice(current_history, "claude-sonnet-4-6"),
                "Gemini": _extract_latest_model_choice(current_history, "gemini-3-flash-preview"),
            }
            if all(v.strip() for v in raw_votes.values()):
                normalized = {k: _normalize_choice_label(v) for k, v in raw_votes.items()}
                uniq = set(normalized.values())
                if len(uniq) == 1:
                    consensus = next(iter(uniq))
                    return {
                        "status": "만장일치",
                        "conclusion": f"{consensus} (규칙 기반 판정)"
                    }
                return {"status": "불합치", "votes": raw_votes}

            if not local_available:
                fallback_votes = {
                    "GPT": raw_votes.get("GPT", "").strip() or "판독 실패",
                    "Claude": raw_votes.get("Claude", "").strip() or "판독 실패",
                    "Gemini": raw_votes.get("Gemini", "").strip() or "판독 실패",
                }
                return {"status": "불합치", "votes": fallback_votes}

            # 2) 규칙 기반 불가 시 로컬 모델 판정
            sys_prompt = (
                "너는 수석 투자 판사야. AI들의 3턴 연속 논쟁 및 1문장 최종 결론(특히 '[최종 선택: ...]' 부분) 기록을 읽어봐.\n"
                "주제가 'A에 투자할까 B에 투자할까?' 일 수도 있고, 단순 찬반 논쟁일 수도 있어.\n"
                "세 AI의 최종 선택이 의미상 '완전히 동일한 하나의 결론(방향성)'을 가리키는지 (만장일치) 아니면 서로 다른 선택지/자산을 추천하는지 (불합치) 판단해.\n"
                "반드시 JSON 포맷만 출력해.\n"
                "만장일치인 경우: {\"status\": \"만장일치\", \"conclusion\": \"만장일치된 최종 선택의 핵심 요약\"}\n"
                "의견이 엇갈린 경우: {\"status\": \"불합치\", \"votes\": {\"GPT\": \"A자산 추천 요지\", \"Claude\": \"B자산 추천 요지\", \"Gemini\": \"관망 추천 요지 등\"}}"
            )
            raw = await self.llm.get_local_response(sys_prompt, current_history)
            parsed = parse_json_object(raw) or {}
            if validate_unanimity_payload(parsed):
                return parsed

            retry_prompt = (
                "아래 출력은 JSON 형식이 깨졌습니다. 반드시 단일 JSON 객체로만 다시 출력하세요.\n"
                "필드 규격: 만장일치 => {\"status\":\"만장일치\",\"conclusion\":\"...\"}\n"
                "불합치 => {\"status\":\"불합치\",\"votes\":{\"GPT\":\"...\",\"Claude\":\"...\",\"Gemini\":\"...\"}}\n\n"
                f"[깨진 출력]\n{raw}"
            )
            raw_retry = await self.llm.get_local_response("JSON 복구기", retry_prompt)
            parsed_retry = parse_json_object(raw_retry) or {}
            if validate_unanimity_payload(parsed_retry):
                return parsed_retry
            return {"status": "ParseError", "raw": raw_retry}

        async def get_judge_verdict_final(current_history: str) -> dict:
            await ctx.send("⚖️ **[로컬 수석 판사 최종 다수결 및 대립 양상 판결 돌입 중...]**")
            if not local_available:
                votes = {
                    "GPT": _extract_latest_model_choice(current_history, "gpt-5.2-2025-12-11") or "판독 실패",
                    "Claude": _extract_latest_model_choice(current_history, "claude-sonnet-4-6") or "판독 실패",
                    "Gemini": _extract_latest_model_choice(current_history, "gemini-3-flash-preview") or "판독 실패",
                }
                counts: dict[str, int] = {}
                for v in votes.values():
                    norm = _normalize_choice_label(v)
                    counts[norm] = counts.get(norm, 0) + 1
                top_choice = max(counts.items(), key=lambda x: x[1])[0]
                top_count = counts[top_choice]
                return {
                    "status": f"{top_count}:{3-top_count}결론(강등 모드)",
                    "majority_choice": f"{top_choice} (로컬 판사 비활성으로 규칙 기반 집계)",
                    "logical_winner": "강등 모드에서는 정성 평가를 생략하고 규칙 기반 집계만 제공",
                    "fatal_flaw": "로컬 판사 비활성으로 심층 논리 판독을 수행하지 못함"
                }

            sys_prompt = (
                "너는 월스트리트의 매크로 투자 수석 위원장(로컬 판사)이야. 앞서 3명의 AI 매니저들이 치열하게 토론했고, 만장일치에 실패했어.\n"
                "지금까지의 전체 토론 기록과 팩트시트를 객관적으로 읽고, 다음 3가지를 판결해.\n"
                "1. 다수결(머릿수)의 양상과 요지 \n"
                "2. 머릿수와 무관하게 가장 객관적 팩트(검색 데이터 등)로 승리한 진영(논리적 승자)과 그 이유\n"
                "3. 다수결을 차지한 진영(혹은 논리적 패자)이 범한 치명적인 팩트 오류나 확증 편향 지적\n"
                "반드시 앞뒤 설명 없이 아래 JSON 포맷만 출력해.\n"
                "{\n"
                "  \"status\": \"최종 다수결 현황 (예: 2:1결론)\",\n"
                "  \"majority_choice\": \"다수결 항목과 이유 요약\",\n"
                "  \"logical_winner\": \"논리적 승자의 요지와 왜 그 논증이 팩트 기반으로 가장 우수했는지의 이유\",\n"
                "  \"fatal_flaw\": \"패자(혹은 다수결) 진영이 무시한 치명적 팩트나 억지스러운 맹점(확증 편향) 지적\"\n"
                "}"
            )
            raw = await self.llm.get_local_response(sys_prompt, current_history)
            parsed = parse_json_object(raw) or {}
            if validate_final_verdict_payload(parsed):
                return parsed

            retry_prompt = (
                "아래 출력은 JSON 형식이 깨졌습니다. 반드시 단일 JSON 객체로만 다시 출력하세요.\n"
                "필드: status, majority_choice, logical_winner, fatal_flaw (모두 문자열)\n\n"
                f"[깨진 출력]\n{raw}"
            )
            raw_retry = await self.llm.get_local_response("JSON 복구기", retry_prompt)
            parsed_retry = parse_json_object(raw_retry) or {}
            if validate_final_verdict_payload(parsed_retry):
                return parsed_retry
            return {"status": "ParseError", "raw_text": raw_retry}

        current_history = history
        max_loops = 4
        loop_count = 0
        no_new_evidence_loops = 0
        final_decision: dict = {}

        while loop_count < max_loops:
            current_history, research_count, loop_meta = await run_phase2_debate(current_history, loop_count + 1)
            current_history = await run_phase3_cross_check(current_history, loop_count + 1)

            ack_models = list(dict.fromkeys(loop_meta.get("ack_models", []))) if isinstance(loop_meta, dict) else []
            if ack_models:
                await ctx.send(f"🕊️ **[ACK 감지]** 이번 라운드에서 ACK 표기 모델: {', '.join(ack_models)}")
            full_ack = len(set(ack_models)) >= 3

            if research_count == 0 and loop_count >= 1:
                no_new_evidence_loops += 1
            else:
                no_new_evidence_loops = 0
            
            verdict = await check_unanimity(current_history)
            status = verdict.get("status", "")
            
            if status == "만장일치":
                if len(session_searched_queries) == 0:
                    auto_query = user_query.strip()[:180]
                    norm_auto = re.sub(r"\s+", " ", auto_query.lower())
                    await ctx.send("🔍 **[근거 강제 확보]** 만장일치 이전에 최소 1회 SEARCH 근거를 확보합니다.")
                    package = None
                    try:
                        if research_cache_ttl_hours > 0:
                            cached = self.db.get_cached_research_evidence(auto_query, max_age_hours=research_cache_ttl_hours)
                            if isinstance(cached, dict):
                                package = cached
                                await ctx.send("⚡ **[리서치 캐시 히트]** 강제 근거도 캐시를 재사용합니다.")
                        if not package:
                            package = await self.checker.run_deep_research_package(auto_query)
                            self.db.save_research_evidence(user_query, auto_query, package)
                        _register_evidence_ids(auto_query, package)
                        try:
                            self.relation_miner.ingest_evidence_package(user_query, auto_query, package)
                        except Exception:
                            pass
                    except Exception as e:
                        package = {
                            "query": auto_query,
                            "status": "error",
                            "evidences": [],
                            "limitations": [f"강제 리서치 실패: {e}"],
                            "summary": f"강제 리서치 실패: {e}",
                        }
                    session_searched_queries.add(norm_auto)

                    evidences = package.get("evidences", []) if isinstance(package, dict) else []
                    summary = package.get("summary", "") if isinstance(package, dict) else ""
                    source_lines = []
                    for ev in evidences[:5]:
                        ev_id = ev.get("global_evidence_id") or ev.get("evidence_id")
                        source_lines.append(f"- {ev_id}: {ev.get('title')} ({ev.get('domain')})\n  {ev.get('url')}")
                    current_history += (
                        f"\n[시스템 강제 리서치 데이터: '{auto_query}']\n"
                        f"- status: {package.get('status') if isinstance(package, dict) else 'unknown'}\n"
                        f"- evidence_count: {len(evidences)}\n"
                        f"- summary: {summary}\n"
                        f"- sources:\n{chr(10).join(source_lines) if source_lines else '- 출처 없음'}\n\n"
                    )
                    loop_count += 1
                    if loop_count < max_loops:
                        await ctx.send("🔄 **[근거 반영 재검증]** 강제 확보한 SEARCH 근거를 반영해 최종 결론을 다시 도출합니다.")
                        continue

                final_decision = verdict
                await send_chunked(ctx, f"🎉 **[만장일치 도출 성공!]**\n```json\n{json.dumps(verdict, indent=2, ensure_ascii=False)}\n```")
                break
            else:
                await send_chunked(ctx, f"⚠️ **[의견 불합치 발생 - Loop {loop_count + 1}]**\n```json\n{json.dumps(verdict, indent=2, ensure_ascii=False)}\n```")
                loop_count += 1
                if full_ack:
                    await ctx.send("🛑 **[루프 조기 종료]** 전 모델 ACK가 감지되어 추가 반박 루프를 종료하고 판사 최종 판결로 전환합니다.")
                    break
                if no_new_evidence_loops >= 1:
                    await ctx.send("🛑 **[루프 조기 종료]** 새 증거가 추가되지 않아 추가 토론의 실익이 낮다고 판단했습니다. 최종 판사 판결로 전환합니다.")
                    break
                if loop_count < max_loops:
                    await ctx.send("🔄 **[만장일치 실패. 한 번 더 서로의 논리를 교차 검증하고 토론을 재개합니다.]**")

        if not final_decision or final_decision.get("status", "") != "만장일치":
            final_decision = await get_judge_verdict_final(current_history)
            
            # JSON 덩어리 대신 가독성 좋은 자연어/마크다운 포맷으로 유저에게 출력
            if "raw_text" in final_decision and final_decision.get("status") == "ParseError":
                await send_chunked(ctx, f"👨‍⚖️ **[로컬 수석 판사 최종 판독 결과]**\n{final_decision['raw_text']}")
            else:
                msg = (
                    f"💡 **[최종 다수결 판결: {final_decision.get('status', '알 수 없음')}]**\n\n"
                    f"📌 **다수 의견 요약**: {final_decision.get('majority_choice', '없음')}\n\n"
                    f"🏆 **논리적 승자 (팩트 기반 가장 우수한 논증)**:\n{final_decision.get('logical_winner', '없음')}\n\n"
                    f"⚠️ **다수결의 함정 (치명적 팩트 오류/확증 편향 지적)**:\n{final_decision.get('fatal_flaw', '없음')}"
                )
                await send_chunked(ctx, msg)

        history = current_history

        # 결과 DB 저장 및 마무리
        history += f"\n[최종 판결 결과]: {json.dumps(final_decision, ensure_ascii=False)}"
        status_val = final_decision.get("status", "Unknown") if final_decision else "Unknown"
        debate_id = self.db.save_debate(user_query, history, status_val, final_decision) # type: ignore
        await ctx.send("✅ `오늘의 복합 심층 토론 기록이 정상 저장되었습니다.`")
        
        return history, debate_id # type: ignore
