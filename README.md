# AI Group Chat Bot
Discord-based multi-LLM (ChatGPT, Claude, Gemini, Local) group chat platform for investment analysis and consultation.

## 🚀 주요 기능 및 명령어 (Features & Commands)
- `!토론 [주제]`: 3개의 프론티어 AI(GPT, Claude, Gemini)와 로컬 모델(gpt-oss:20b)이 교차 반박 토론을 수행합니다. `[SEARCH: ...]`가 발생하면 Evidence 패키지(URL/발췌/도메인)가 생성 및 저장됩니다.
- `!질문 [과거 맥락 질문]`: 하이브리드 RAG (키워드 추출 + SQLite FTS 우선 검색 + LIKE fallback) 시스템을 통해 수개월 전의 회의록, 판결문, 요약본을 뒤져 가장 정확한 인사이트를 가져옵니다.
- `!뉴스`: 백그라운드 스케줄러(`scraper_job.py`)가 매일 수집해둔 방대한 뉴욕타임스 프리미엄 글로벌 뉴스를 디스코드에서 바로 읽기 좋게 끊어(Chunking) 보여줍니다.
- `!포트폴리오`: 로컬 포트폴리오 파일을 로드/파싱하고, 이후 `!토론`에서 LLM 컨텍스트로 자동 주입합니다.
- `!포트변동`: 보유 종목의 현재가 기준 손익(PnL) 스냅샷을 계산해 보여줍니다.

## 🧠 아키텍처 개요
- **Decoupled Job Scheduling**: 뉴스 스크래핑과 데이터 요약(Daily, Weekly, Monthly)은 봇 내부 루프가 아닌 가벼운 Windows Task Scheduler 기반(`.bat`)으로 분리되어 메인 디스코드 봇은 응답 대기에만 집중합니다.
- **Hybrid RAG Layer**: 토론 파이프라인(Phase 0)에 RAG가 결합되어 과거 실패/성공 사례를 이번 토론의 바탕(Base Argument)으로 씁니다.
- **Anti-Hallucination**: 외부 API 장애(529 Overloaded 등) 발생 시 안전한 Retry 로직을 갖추었고, 토론은 내부 사고 노출 없이 근거 중심 출력으로 제한됩니다. 또한 리서치 결과는 Evidence 패키지 형태로 저장되어 재검증이 가능합니다.
- **Evidence-ID Enforcement**: 최종 변론에는 `[근거ID: EVxxxx]` 태그를 강제해 근거 없는 결론 출력을 줄입니다.
- **Targeted Rebuttal**: `[조준:Model]` 태그로 즉각 방어 라운드를 실행하고, `[ACK]` 감지 시 불필요한 반복 루프를 단축합니다.
- **Gemini Auto Fallback**: `gemini-3.1-pro-preview`를 우선 호출하고, 타임아웃/과부하/비정상 응답 시 자동으로 `gemini-3-flash-preview`로 폴백합니다.
- **Degraded Mode**: 로컬 모델(Ollama) 장애 시 시스템은 강등 모드로 전환되어 규칙 기반 판정/기본 리서치를 유지하며, 장애 상태를 채널에 명확히 안내합니다.
- **Ontology-Aware Planning**: 토론 시작 전에 온톨로지 플래너가 엔티티 링크/관계 확장 기반으로 `tickers`, `web_queries`, `rag_keywords`를 구성합니다.
- **Ontology Auto-Relation Mining**: SEARCH evidence에서 관계(`supplies_to`, `competes_with` 등)를 자동 추출해 온톨로지를 갱신합니다.

## 🔐 보안 주의
- 세션 쿠키 파일(`src/data_fetcher/cookies.local.json`)은 개인 로컬에만 두고 절대 커밋하지 마세요.
- 예시 포맷은 `src/data_fetcher/cookies.local.example.json`를 참고하세요.

## ⚙️ 설치 메모
- 의존성 설치: `pip install -r requirements.txt`
- Playwright 브라우저 설치(필요 시): `playwright install`
- 속도전 모드(선택): `DEBATE_SPEED_MODE=first_completed`
- 리서치 캐시(선택): `RESEARCH_CACHE_TTL_HOURS=12`
- 웹 fetch 병렬수(선택): `WEB_FETCH_CONCURRENCY=4`
- LLM 회로 차단기(선택): `CIRCUIT_FAILURE_THRESHOLD=3`, `CIRCUIT_COOLDOWN_SEC=60`

## 🧾 포트폴리오 입력 포맷
- 기본 파일 경로: `data/my_portfolio.md` (`PORTFOLIO_FILE_PATH`로 변경 가능)
- 권장 라인 포맷 예시:
  - `NVDA | qty: 3 | avg: 780`
  - `005930.KS, 12, 71200`
  - `TSLA 2 @ 250`
- 또는 JSON 블록:
````text
```portfolio-json
[
  {"ticker":"NVDA","qty":3,"avg_price":780,"currency":"USD"},
  {"ticker":"005930.KS","qty":12,"avg_price":71200,"currency":"KRW"}
]
```
````

## 🧩 Ontology 레이어
- 온톨로지+RAG+웹검색 통합 워크플로우 문서: `ONTOLOGY_RAG_WEB_WORKFLOW.md`
- 부트스트랩 로더: `python src/ontology_bootstrap.py --help`

## 📘 문서 인덱스
- 토론 동작 상세: `docs_debate_process.md`
- DB 스키마/저장 정책: `data_schema_and_tracking_guide.md`
- 기능/스택/포트폴리오 사용 가이드: `FEATURES_AND_PORTFOLIO_GUIDE.md`
- 최신 변경 이력: `SYSTEM_UPDATE_NOTES_2026-03-03.md`
- 재가동 가이드: `System Restart Guide`
