import asyncio
import json
import re
import random
import db_manager
from data_fetcher.pipeline import MasterDataPipeline
from rag_agent import RAGAgent

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

    async def run_full_debate(self, ctx, user_query: str):
        """메인 봇에서 호출되는 2~3라운드 토론 전체 로직"""
        import os
        import glob
        import json
        
        await ctx.send(f"🔍 `{user_query}` 주제 분석 및 방향성 설정 중...")
        history = f"**[주제]** {user_query}\n\n"
        
        # 0. RAG 기반 과거 기록 조회 및 투입
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
            
        # 1. 쿼리 성격 분류 (로컬 모델 활용)
        classify_prompt = (
            "다음 사용자의 토론 주제가 '최신 기술/시장 이슈, 기업 동향, 미래 전망' 등 최신 배경지식이 필수적인 주제인지, "
            "아니면 '자산 배분 이론, 일반적인 투자 철학, 단순 원론' 등 보편적 이론에 가까운지 판단해.\n"
            "최신 뉴스/전망이 필요하면 {\"is_recent_issue\": true} 를, 필요하지 않으면 {\"is_recent_issue\": false} 를 JSON 형식으로 출력해."
        )
        
        try:
            class_json_str = await self.llm.get_local_response(classify_prompt, user_query)
            match = re.search(r'\{.*\}', class_json_str, flags=re.DOTALL)
            is_recent_issue = True
            if match:
                is_recent_issue = json.loads(match.group(0)).get("is_recent_issue", True)
        except Exception:
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
                await ctx.send("⚠️ 수집된 프리미엄 뉴스 백업 파일이 없습니다.")
        else:
            await ctx.send("💡 **[일반 투자 철학/원론적 토론 모드]** (최신 뉴스보다 보편적인 투자 논리에 집중합니다.)")
        
        roles = {
            "gpt-5.2-2025-12-11": "찬성 또는 공격적 성장 모델 지지",
            "claude-sonnet-4-6": "안전/가치 투자 및 반대/리스크 부각",
            "gemini-3-flash-preview": "제3의 시각 및 논리적 오류 저격"
        }
        
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
            extract_json_str = await self.llm.get_local_response(extract_prompt, user_query)
            # JSON 파싱 
            match = re.search(r'\{.*\}', extract_json_str, flags=re.DOTALL)
            if match:
                extract_data = json.loads(match.group(0))
                tickers = extract_data.get("tickers", [])
                searches = extract_data.get("searches", [])
                
                # 이전 버전 호환 (search 문자열 하나만 내려올 경우)
                if "search" in extract_data and isinstance(extract_data["search"], str):
                    searches.append(extract_data["search"])
                    
                if tickers or searches:
                    await ctx.send(f"📊 **[초고밀도 파이프라인 가동]** 티커: {tickers}, 다중 검색: {searches}")
                    # 새로운 초고밀도 다중 마스터 파이프라인에서 데이터 동시 수집
                    fact_sheet = await self.data_pipeline.build_ultimate_fact_sheet(tickers, searches)
                    
        except Exception as e:
            await ctx.send(f"⚠️ 리서치 추출 중 오류: {e}")
            
        if not fact_sheet.strip():
            fact_sheet = "현재 주제에 대해 별도로 검색된 숫자 및 팩트 데이터가 없습니다. 이미 알고 있는 지식을 활용하세요."
            
        history += f"**[공통 Fact-Sheet]**\n{fact_sheet}\n\n"
        await send_chunked(ctx, f"📄 **[공통 Fact-Sheet 지급 완료]**\n(해당 팩트를 기반으로 토론이 전개됩니다.)\n")

        methods = {
            "gpt-5.2-2025-12-11": self.llm.get_gpt_response,
            "claude-sonnet-4-6": self.llm.get_claude_response,
            "gemini-3-flash-preview": self.llm.get_gemini_response
        }

        async def run_phase2_debate(current_history: str, turn_idx: int) -> str:
            gpt_name = "gpt-5.2-2025-12-11"
            claude_name = "claude-sonnet-4-6"
            gemini_name = "gemini-3-flash-preview"

            if turn_idx == 1:
                # Loop 1: 완전 블라인드(독립적) 병렬 생성
                await ctx.send(f"🔥 **[Phase 2 (Loop {turn_idx}): 블라인드 동시 논증 (각자 독립적 관점)]**")
                dyn_hist = current_history
                base_inst = "너는 세계 최고 수준의 월스트리트 헤지펀드 매니저야. 제공된 Fact-Sheet를 바탕으로 철저하고 뾰족하게 논증해. 다른 관점에 휘둘리지 않고 오직 너의 스탠스를 3~4문장으로 강력히 주장해. 반드시 <thought> 안에 내부 사고과정을 적어."
                
                async def fetch_blind(model_name: str) -> tuple[str, str]:
                    try:
                        prompt = f"{base_inst} 너의 스탠스는 '{roles[model_name]}'이야."
                        reply = await methods[model_name](prompt, dyn_hist)
                        return model_name, str(reply)
                    except Exception as e:
                        return model_name, f"<thought>에러 발생</thought> {e}"
                
                tasks = [
                    fetch_blind(gpt_name),
                    fetch_blind(claude_name),
                    fetch_blind(gemini_name)
                ]
                
                # 동시에 의견 도출 (서로의 의견을 모름)
                results = await asyncio.gather(*tasks)
                for model_n, rep_text in results:
                    # 내부 사고(<thought>)는 디스코드 UI에만 송출하고 공통 히스토리에서는 제거
                    clean_rep = re.sub(r'<thought>.*?</thought>', '', str(rep_text), flags=re.DOTALL).strip()
                    dyn_hist += f"[{model_n} {turn_idx}R 최초 발언]:\n{clean_rep}\n\n"
                    await send_chunked(ctx, f"🗣️ **[{model_n} 블라인드 발언]**\n{rep_text}")
                    
                return dyn_hist

            else:
                # Loop 2부터: 순차적 크로스 반박 (서로의 의견을 보고 타격)
                await ctx.send(f"🔥 **[Phase 2 (Loop {turn_idx}): 이전 라운드를 병합한 순차적 릴레이 반박]**")
                dyn_hist = current_history
                base_inst = (
                    "너는 세계 최고 수준의 월스트리트 헤지펀드 매니저야. 이미 앞선 라운드들에서 다른 매니저들의 주장과 논란이 나왔어. "
                    "상대방 주장의 맹점을 철저히 공격하며 너의 논리를 강화해. 주어진 팩트체크 데이터를 인용해. "
                    "만약 확실한 판단을 위해 직접 확인해야 할 기사의 원문이나 인터넷 웹 검색이 필요하다면 답변 마지막 줄에 '[SEARCH: 검색할 구체적인 키워드]' 라고 적어둬. "
                    "3~4문장으로 작성하고, 반드시 <thought> 안에 내부 사고과정을 적어.\n"
                    "[치명적 규칙 (Anti-Anchoring)]: 토론 중 상대방이나 자신이 [SEARCH]를 통해 실시간 검색 팩트(예: 실적 하향, 새로운 수치)를 가져와 너의 기존 논거나 전제가 깨졌다면, 억지를 부리지 말고 유연하게 수용해라.\n"
                    "[추가 강력 지시 (맞불 리서치)]: 만약 상대방이 [SEARCH]로 너에게 불리한 팩트를 들이밀었다면, 너도 방어하거나 반박하기 위해 그 팩트의 진위를 검증하는 [SEARCH: 반대 관점 키워드]를 적극적으로 사용하여 반격해라! 팩트 없이 말로만 우기면 무조건 패배한다."
                )

                # 이미 수행된 리서치 키워드를 추적하여 중복 검색 및 토큰 누수 차단
                searched_queries = set()
                
                # 보조 함수: 요청에 [SEARCH: xxx] 가 있는지 확인하고 리서치 수행
                async def handle_research_request(model_n: str, text: str, hist: str) -> str:
                    match = re.search(r'\[SEARCH:\s*(.+?)\]', text)
                    if match:
                        query = match.group(1).strip()
                        # 이미 이번 토론에서 검색된 키워드와 비슷하다면 건너뛰어 토큰 폭발 방지
                        if query in searched_queries:
                            await ctx.send(f"🕵️‍♂️ **[심층 리서치 보류]** '{query}'는 이미 검색된 맥락입니다. 기존 데이터를 재활용합니다.")
                            return hist
                            
                        searched_queries.add(query)
                        await ctx.send(f"🕵️‍♂️ **[심층 리서치 발동]** {model_n}의 요청으로 웹 원문 검색 중... 🔍 `{query}`")
                        research_result = await self.checker.run_deep_research(query)
                        hist += f"\n[시스템 긴급 투입 데이터: '{query}' 웹 리서치 요약]\n{research_result}\n\n"
                        await send_chunked(ctx, f"📜 **[리서치 완료]** '{query}'에 대한 팩트가 토론장에 전송되었습니다.")
                    return hist

                # Turn 1: GPT 
                await ctx.send(f"🗣️ **[{gpt_name} 반박 및 논증 갱신 중...]**")
                gpt_prompt = f"{base_inst} 너의 스탠스는 '{roles[gpt_name]}'이야."
                try:
                    gpt_rep = await methods[gpt_name](gpt_prompt, dyn_hist)
                except Exception as e:
                    gpt_rep = f"<thought>에러 발생</thought> {e}"
                
                clean_gpt = re.sub(r'<thought>.*?</thought>', '', str(gpt_rep), flags=re.DOTALL).strip()
                dyn_hist += f"[{gpt_name} {turn_idx}R 주장]:\n{clean_gpt}\n\n"
                await send_chunked(ctx, f"🗣️ **[{gpt_name}]**\n{gpt_rep}")
                dyn_hist = await handle_research_request(gpt_name, str(gpt_rep), dyn_hist)

                # Turn 2: Claude 
                await ctx.send(f"🗣️ **[{claude_name} 앞선 발언 타격 중...]**")
                claude_prompt = f"{base_inst} 너의 스탠스는 '{roles[claude_name]}'이야."
                try:
                    claude_rep = await methods[claude_name](claude_prompt, dyn_hist)
                except Exception as e:
                    claude_rep = f"<thought>에러 발생</thought> {e}"
                
                clean_claude = re.sub(r'<thought>.*?</thought>', '', str(claude_rep), flags=re.DOTALL).strip()
                dyn_hist += f"[{claude_name} {turn_idx}R 반박]:\n{clean_claude}\n\n"
                await send_chunked(ctx, f"🗣️ **[{claude_name}]**\n{claude_rep}")
                dyn_hist = await handle_research_request(claude_name, str(claude_rep), dyn_hist)

                # Turn 3: Gemini 
                await ctx.send(f"🗣️ **[{gemini_name} 제3의 시각으로 국면 전환 중...]**")
                gemini_prompt = f"{base_inst} 너의 스탠스는 '{roles[gemini_name]}'이야. 팩트 데이터에 어긋난 억지 주장을 펼친 쪽을 폭격해."
                try:
                    gemini_rep = await methods[gemini_name](gemini_prompt, dyn_hist)
                except Exception as e:
                    gemini_rep = f"<thought>에러 발생</thought> {e}"
                
                clean_gemini = re.sub(r'<thought>.*?</thought>', '', str(gemini_rep), flags=re.DOTALL).strip()
                dyn_hist += f"[{gemini_name} {turn_idx}R 타격]:\n{clean_gemini}\n\n"
                await send_chunked(ctx, f"🗣️ **[{gemini_name}]**\n{gemini_rep}")
                dyn_hist = await handle_research_request(gemini_name, str(gemini_rep), dyn_hist)
                
                return dyn_hist

        # ==========================================
        # Phase 3: 무자비한 크로스 체크 및 1문장 최종 결론
        # ==========================================
        async def run_phase3_cross_check(current_history: str, turn_idx: int) -> str:
            await ctx.send(f"🔥 **[Phase 3 (Loop {turn_idx}): 크로스 체크 및 최종 변론 (동시 진행)]**")
            
            final_prompt = "지금까지의 모든 쟁점, 상대방의 공격, Fact-Sheet 데이터를 종합하여 최종 변론을 해. 더 이상의 반박이 불가능하도록 자신의 스탠스를 방어하는 확고한 1문장을 도출해. 글의 맨 마지막에 반드시 [최종 선택: (나의 핵심 주장을 1~3단어로 요약)] 키워드를 달아. (예: [최종 선택: 엔비디아 비중 확대], [최종 선택: 관망 및 현금 확보] 등)"
            
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
                    
            tasks = [
                fetch_final("gpt", gpt_name),
                fetch_final("claude", claude_name),
                fetch_final("gemini", gemini_name)
            ]
            
            final_results = await asyncio.gather(*tasks)
            for model_n, rep_text in final_results:
                final_hist += f"[{model_n} {turn_idx}R 최종 결론]: {rep_text}\n\n"
                await send_chunked(ctx, f"🎯 **[{model_n} 최종 결론]**\n{rep_text}")
                
            return final_hist

        # ==========================================
        # Phase 4: 로컬 수석 판사(gpt-oss-20b)의 최종 판정 및 루프 컨트롤
        # ==========================================
        async def check_unanimity(current_history: str) -> dict:
            await ctx.send("⚖️ **[로컬 수석 판사 1차 판정: 만장일치 여부 확인 중...]**")
            sys_prompt = (
                "너는 수석 투자 판사야. AI들의 3턴 연속 논쟁 및 1문장 최종 결론(특히 '[최종 선택: ...]' 부분) 기록을 읽어봐.\n"
                "주제가 'A에 투자할까 B에 투자할까?' 일 수도 있고, 단순 찬반 논쟁일 수도 있어.\n"
                "세 AI의 최종 선택이 의미상 '완전히 동일한 하나의 결론(방향성)'을 가리키는지 (만장일치) 아니면 서로 다른 선택지/자산을 추천하는지 (불합치) 판단해.\n"
                "반드시 JSON 포맷만 출력해.\n"
                "만장일치인 경우: {\"status\": \"만장일치\", \"conclusion\": \"만장일치된 최종 선택의 핵심 요약\"}\n"
                "의견이 엇갈린 경우: {\"status\": \"불합치\", \"votes\": {\"GPT\": \"A자산 추천 요지\", \"Claude\": \"B자산 추천 요지\", \"Gemini\": \"관망 추천 요지 등\"}}"
            )
            res = await self.llm.get_local_response(sys_prompt, current_history)
            try:
                parsed = json.loads(res.replace('```json', '').replace('```', '').strip())
                return parsed
            except:
                return {"status": "ParseError", "raw": res}

        async def get_judge_verdict_final(current_history: str) -> dict:
            await ctx.send("⚖️ **[로컬 수석 판사 최종 다수결 및 대립 양상 판결 돌입 중...]**")
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
            res = await self.llm.get_local_response(sys_prompt, current_history)
            try:
                parsed = json.loads(res.replace('```json', '').replace('```', '').strip())
                return parsed
            except:
                return {"status": "ParseError", "raw_text": res}

        current_history = history
        max_loops = 4
        loop_count = 0
        final_decision: dict = {}

        while loop_count < max_loops:
            current_history = await run_phase2_debate(current_history, loop_count + 1)
            current_history = await run_phase3_cross_check(current_history, loop_count + 1)
            
            verdict = await check_unanimity(current_history)
            status = verdict.get("status", "")
            
            if status == "만장일치":
                final_decision = verdict
                await send_chunked(ctx, f"🎉 **[만장일치 도출 성공!]**\n```json\n{json.dumps(verdict, indent=2, ensure_ascii=False)}\n```")
                break
            else:
                await send_chunked(ctx, f"⚠️ **[의견 불합치 발생 - Loop {loop_count + 1}]**\n```json\n{json.dumps(verdict, indent=2, ensure_ascii=False)}\n```")
                loop_count += 1
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
