# AI Group Chat Bot
Discord-based multi-LLM (ChatGPT, Claude, Gemini, Local) group chat platform for investment analysis and consultation.

## 🚀 주요 기능 및 명령어 (Features & Commands)
- `!토론 [주제]`: 3개의 프론티어 AI(GPT, Claude, Gemini)와 1개의 로컬 팩트체커(GPT-OSS)가 실시간으로 데이터를 스크래핑하고 속도전(동시 반박)으로 토론하여 `investment_json` 결과를 산출합니다.
- `!질문 [과거 맥락 질문]`: 하이브리드 RAG (키워드 추출 + SQLite Full Text Search) 시스템을 통해 수개월 전의 회의록, 판결문, 요약본을 뒤져 가장 정확한 인사이트를 가져옵니다.
- `!뉴스`: 백그라운드 스케줄러(`scraper_job.py`)가 매일 수집해둔 방대한 뉴욕타임스 프리미엄 글로벌 뉴스를 디스코드에서 바로 읽기 좋게 끊어(Chunking) 보여줍니다.

## 🧠 아키텍처 개요
- **Decoupled Job Scheduling**: 뉴스 스크래핑과 데이터 요약(Daily, Weekly, Monthly)은 봇 내부 루프가 아닌 가벼운 Windows Task Scheduler 기반(`.bat`)으로 분리되어 메인 디스코드 봇은 응답 대기에만 집중합니다.
- **Hybrid RAG Layer**: 토론 파이프라인(Phase 0)에 RAG가 결합되어 과거 실패/성공 사례를 이번 토론의 바탕(Base Argument)으로 씁니다.
- **Anti-Hallucination**: 외부 API 장애(529 Overloaded 등) 발생 시 안전한 Retry 로직을 갖추었으며, 토론 중 AI들의 속마음(`<thought>`)이 공유되어 토큰이 누수되거나 컨닝하는 것을 원천 차단했습니다.
