# 기능/스택/포트폴리오 사용 가이드

이 문서는 현재 코드 기준으로 다음을 빠르게 확인하기 위한 운영 안내입니다.
1. 최근 추가/개선 기능
2. 기술 스택
3. 포트폴리오 명령어 사용법
4. 포트폴리오 저장 포맷

## 1) 최근 추가/개선 기능
1. Gemini 자동 폴백
- `gemini-3.1-pro-preview` 우선 호출 후 실패 시 `gemini-3-flash-preview`로 자동 폴백
- 관련 설정: `GEMINI_PRIMARY_MODEL`, `GEMINI_FALLBACK_MODEL`, `GEMINI_TIMEOUT_SEC`
- 구현 파일: `src/llm_client.py`

2. 웹 리서치 병렬화
- 검색 결과 URL 본문 fetch를 동시 처리하여 SEARCH 응답 지연 감소
- 관련 설정: `WEB_FETCH_CONCURRENCY`, `WEB_FETCH_TIMEOUT_SEC`
- 구현 파일: `src/web_search_agent.py`

3. 리서치 캐시
- 동일/유사 쿼리에 대해 TTL 내 기존 Evidence 재사용
- 관련 설정: `RESEARCH_CACHE_TTL_HOURS`
- 구현 파일: `src/db_manager.py`, `src/debate_manager.py`

4. LLM 회로 차단기
- 모델별 연속 실패 시 일정 시간 호출 차단으로 토큰/시간 낭비 방지
- 관련 설정: `CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_COOLDOWN_SEC`
- 구현 파일: `src/llm_client.py`

5. Evidence-ID 강제
- 최종 결론에 `[근거ID: EVxxxx]` 태그 강제
- 구현 파일: `src/debate_manager.py`

6. 토론 제어 태그
- `[ACK]` 감지 시 루프 조기 종료
- `[조준:Model]` 감지 시 타깃 모델 즉각 방어
- 구현 파일: `src/debate_manager.py`

7. 온톨로지 자동 관계추출
- SEARCH evidence 텍스트에서 관계를 추출해 ontology relation 갱신
- 구현 파일: `src/ontology/relation_miner.py`

8. 포트폴리오 로드/변동 명령
- `!포트폴리오`, `!포트변동` 추가
- `!토론` 시 포트폴리오 컨텍스트 자동 주입
- 구현 파일: `src/portfolio_manager.py`, `src/main.py`, `src/debate_manager.py`

## 2) 기술 스택
1. 언어/런타임
- Python 3.x
- Discord Bot: `discord.py`

2. LLM/API
- OpenAI (`openai`)
- Anthropic (`anthropic`)
- Google Gemini (`google-genai`)
- Local LLM (Ollama `/api/chat`)

3. 데이터/저장
- SQLite (`data/investment_bot.db`)
- FTS5 인덱스 (`debates_fts`, `summaries_fts`)
- Evidence 저장 테이블 (`research_evidences`)

4. 시장/뉴스/수집
- 가격/기초 데이터: `yfinance`
- 뉴스/API 호출: `requests`, `duckduckgo-search`
- HTML 파싱: `beautifulsoup4`
- 프리미엄 뉴스 자동 수집: `playwright` + NYT API

5. 기타
- 환경변수: `python-dotenv`
- 기술지표: `pandas_ta`

## 3) 디스코드 명령어
1. `!토론 [주제]`
- 멀티 모델 토론 실행
- SEARCH/Evidence 기반 팩트 강화
- 채널에 포트폴리오 컨텍스트가 있으면 자동 주입

2. `!질문 [질문]`
- 과거 토론/요약 RAG 검색 응답

3. `!뉴스`
- 최신 `news_archive/premium_news_YYYYMMDD.txt` 출력

4. `!포트폴리오`
- 포트폴리오 파일 로드/파싱
- 파싱 결과 출력
- 이후 같은 채널의 `!토론`에 자동 주입

5. `!포트변동`
- 보유 종목 현재가 기준 PnL 스냅샷 출력
- `yfinance` 미설치 시 안내 메시지 출력

## 4) 포트폴리오 저장 방식
1. 기본 파일 경로
- 기본: `data/my_portfolio.md`
- 변경: `.env`의 `PORTFOLIO_FILE_PATH`

2. 권장 입력 포맷 (수기 친화)
- `NVDA | qty: 3 | avg: 780`
- `005930.KS, 12, 71200`
- `TSLA 2 @ 250`

3. JSON 포맷 (정확 파싱)
````text
```portfolio-json
[
  {"ticker":"NVDA","qty":3,"avg_price":780,"currency":"USD"},
  {"ticker":"005930.KS","qty":12,"avg_price":71200,"currency":"KRW"}
]
```
````

4. 파싱 규칙 요약
- 티커 정규화: `005930` 입력 시 `005930.KS` 보정
- 통화 추정: `.KS/.KQ`는 기본 KRW, 그 외 기본 USD
- 동일 티커는 수량/평단 가중평균으로 집계

5. 샘플 파일
- `my_portfolio.example.md`

## 5) 운영 체크리스트
1. 필수 패키지 설치
- `pip install -r requirements.txt`

2. 포트변동 기능 사용 시
- `yfinance` 설치 확인

3. 환경변수 점검
- `.env.example` 기준으로 `.env` 구성
- 최소: `DISCORD_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`

4. Windows 스케줄러
- `run_news.bat`, `run_daily.bat`, `run_weekly.bat`, `run_monthly.bat` 등록
