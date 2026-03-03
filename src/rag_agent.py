import json
import re
from db_manager import DBManager
from llm_client import LLMClientManager

class RAGAgent:
    def __init__(self, llm_manager: LLMClientManager):
        self.llm = llm_manager
        self.db = DBManager()

    async def answer_question(self, user_question: str) -> str:
        """
        사용자의 질문을 기반으로 하이브리드 RAG (키워드 추출 -> DB 검색 -> 맥락 기반 답변 생성)를 수행합니다.
        """
        # 1. 사용자 질문에서 독립적인 핵심 키워드(토큰) 추출
        # 키워드 추출용 독립 System Prompt (이식성 고려)
        extract_prompt = (
            "너는 정보 검색기(RAG)의 '쿼리 최적화 모듈'이야. 문장을 분석해 DB에서 검색하기 가장 좋은 명사 키워드만 최대 3개 뽑아내.\n"
            "- '어때', '알려줘', '결론' 같은 서술어나 불용어는 절대 포함하지 마.\n"
            "- 주식 티커명(NVDA, TSLA), 매크로 지표(금리, VIX, 인플레), 섹터(방산, 반도체) 위주로 뽑아내.\n"
            "반드시 아래 JSON 포맷으로만 출력할 것.\n"
            "{\"keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"]}"
        )
        
        extract_res = await self.llm.get_local_response(extract_prompt, user_question)
        
        keywords = []
        try:
            match = re.search(r'\{.*\}', extract_res, flags=re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                keywords = parsed.get("keywords", [])
        except Exception:
            # 파싱 실패 시 띄어쓰기로 분리된 단어들 중 길이가 2 이상인 것만 임의로 사용
            keywords = [w for w in user_question.split() if len(w) >= 2][:3]

        if not keywords:
            keywords = [w for w in user_question.split() if len(w) >= 2][:3]

        print(f"[RAG] 추출된 검색 키워드: {keywords}")

        # 2. SQLite를 이용한 키워드 기반 매칭 (Lexical Search)
        # 여러 키워드 중 하나라도 포함된 토론 기록(investment_json, topic) 조회
        retrieved_contexts = []
        
        for kw in keywords:
            kw_like = f"%{kw}%"
            # 최근 10개의 관련 토론 추출
            self.db.cursor.execute(
                "SELECT date, topic, investment_json FROM debates WHERE topic LIKE ? OR investment_json LIKE ? ORDER BY id DESC LIMIT 10", 
                (kw_like, kw_like)
            )
            rows = self.db.cursor.fetchall()
            for r in rows:
                date, topic, inv_json = r
                context_str = f"[{date}] 토론 주제: {topic}\n[토론 결과 및 판결문]: {inv_json}"
                if context_str not in retrieved_contexts:
                    retrieved_contexts.append(context_str)

        for kw in keywords:
            kw_like = f"%{kw}%"
            # 최근 10개의 관련 일/주/월간 요약 추출
            self.db.cursor.execute(
                "SELECT target_date, summary_type, summary_text FROM summaries WHERE summary_text LIKE ? OR keywords LIKE ? ORDER BY id DESC LIMIT 10", 
                (kw_like, kw_like)
            )
            rows = self.db.cursor.fetchall()
            for r in rows:
                target_date, sum_type, text = r
                context_str = f"[{target_date} {sum_type} 요약]: {text}"
                if context_str not in retrieved_contexts:
                    retrieved_contexts.append(context_str)

        # 3. 검색된 맥락 최적화 및 토큰 엄격 통제 (최대 5개, 각각 핵심만 자름)
        retrieved_contexts = retrieved_contexts[:5]
        
        if not retrieved_contexts:
            return f"🤔 DB 기록 탐색 완료: 제공하신 '{search_keyword_str}' 키워드와 일치하는 과거 토론이나 요약 데이터가 없습니다."

        search_keyword_str = ", ".join(keywords)
        
        # 각 맥락의 길이를 800자로 제한하여 컨텍스트 윈도우 초과 방지
        truncated_contexts = []
        for ctx in retrieved_contexts:
            # ctx는 튜플 구조일 가능성이 있으므로 인덱싱 전에 확인
            ctx_text = ctx if isinstance(ctx, str) else str(ctx)
            if len(ctx_text) > 800:
                truncated_contexts.append(ctx_text[:800] + "...(중략)")
            else:
                truncated_contexts.append(ctx_text)
                
        combined_context = "\n\n---\n\n".join(truncated_contexts)
        
        # 4. 수석 판사 LLM(RAG 모드)을 위한 '절대 규칙' 프롬프트 설계
        sys_prompt = (
            "너는 월스트리트의 '데이터 아카이비스트(RAG AI)'야. 어떠한 선입견이나 외부 지식도 차단해.\n"
            "오직 내가 아래에 제공하는 <CONTEXT_BLOCK>의 데이터들만을 100% 신뢰하여 사용자의 질문에 답변해.\n"
            "\n[절대 규칙 (No Hallucination)]\n"
            "1. <CONTEXT_BLOCK>에 없는 내용은 절대 지어내지 말고, '기록된 바 없습니다'라고 명확히 선을 그어라.\n"
            "2. 과거 토론의 'A 진영 주장', 'B 진영 주장', '최종 판결'을 명확히 구분해서 팩트만 서술하라.\n"
            "3. 답변의 신뢰도를 위해 문장 끝부분에 [YYYY-MM-DD 토론] 등 출처 날짜를 괄호로 표기하라.\n"
            "4. 불필요한 인사말 없이, 전문적이고 간결한 보고서 톤(Markdown 포맷)으로 답변하라."
        )
        
        user_prompt = f"<CONTEXT_BLOCK>\n{combined_context}\n</CONTEXT_BLOCK>\n\n[사용자 질문]: {user_question}"
        
        print(f"[RAG] 로컬 모델 지식 합성 중... (Context chunk count: {len(retrieved_contexts)})")
        final_answer = await self.llm.get_local_response(sys_prompt, user_prompt)
        
        # UI 출력 포매팅
        formatted_answer = f"🔍 **[과거 회의록 스캔 안테나 가동]** (추출된 타겟 키워드: `{search_keyword_str}`)\n\n{final_answer}"
        
        return formatted_answer
