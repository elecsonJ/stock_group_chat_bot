# AI Group Chat Bot
Discord-based multi-LLM (ChatGPT, Claude, Gemini, Local) group chat platform for investment analysis and consultation.

## 🚀 주요 기능 및 명령어 (Features & Commands)
- `!토론 [주제]`: 3개의 프론티어 AI(GPT, Claude, Gemini)와 로컬 모델(`LOCAL_MODEL_NAME`으로 지정)이 교차 반박 토론을 수행합니다. `[SEARCH: ...]`가 발생하면 Evidence 패키지(URL/발췌/도메인)가 생성 및 저장됩니다.
- `!질문 [과거 맥락 질문]`: 하이브리드 RAG (키워드 추출 + SQLite FTS 우선 검색 + LIKE fallback) 시스템을 통해 수개월 전의 회의록, 판결문, 요약본을 뒤져 가장 정확한 인사이트를 가져옵니다.
- `!뉴스`: 다중 소스(NYT + Reuters + SEC + FED)로 수집/정규화/이벤트 클러스터링된 구조화 뉴스 브리핑을 보여줍니다.
- `!포트폴리오`: 로컬 포트폴리오 파일을 로드/파싱하고, 이후 `!토론`에서 LLM 컨텍스트로 자동 주입합니다.
- `!포트변동`: 보유 종목의 현재가 기준 손익(PnL) 스냅샷을 계산해 보여줍니다.
- `!시그널`: 최신 뉴스 이벤트를 웹검색 evidence, source tier, hidden candidate 기준까지 반영해 단기 시그널로 평가합니다. 웹검증 대상은 `SIGNAL_VERIFY_NEW_ONLY` 설정에 따라 달라집니다.
- `!시그널상세 [EVENT_ID]`: 점수/추천 주문/승인 상태를 확인합니다.
- `!승인목록`: 현재 승인 대기 이벤트를 확인합니다.
- `!토론큐`: 자동 토론 큐의 pending/processing 상태를 확인합니다.
- `!토론기록`, `!토론로그 [ID]`: 저장된 자동/수동 토론 기록과 최근 로그를 확인합니다.
- `!토론승인 [EVENT_ID]`, `!토론보류 [EVENT_ID]`, `!토론품질 [ID]`: 비용 게이트에 걸린 자동 토론을 승인/보류하고 토론 품질 점수를 확인합니다.
- `!변화트리거`: 열린 투자 변화 트리거(`add_review`, `reduce_review`, `exit_review`, `hedge_review`)를 확인합니다.
- `!성과`: replay/performance에 누적된 시그널 성과 요약을 확인합니다.
- `!성과피드백 [horizon]`: replay 성과를 signal score/source tier/debate quality 등 조건별 attribution으로 분해해 강화/주의 후보를 확인합니다.
- `!데이터품질 [lookback_hours]`: 뉴스 수집, 웹검증, 뉴스팩, 시그널, replay 측정 커버리지를 점수화해 확인합니다.
- `!이벤트감사 [EVENT_ID]`: 이벤트가 왜 무시/모니터/토론/승인 경로로 갔는지 Intake 로그를 확인합니다.
- `!컨텍스트감사 [consumer]`: RAG/토론/뉴스팩이 AI에게 어떤 컨텍스트를 골랐는지 선택 로그를 확인합니다.
- `!승인 [EVENT_ID]`: 승인 후 즉시 페이퍼 체결을 실행합니다.
- `!거부 [EVENT_ID]`: 승인 대기 이벤트를 거부합니다.
- `!자동매매중지`, `!자동매매재개`, `!가드레일`: 실행 회로 차단기와 주문 한도를 점검합니다.

## 🧠 아키텍처 개요
- **Decoupled Job Scheduling**: 뉴스 스크래핑과 데이터 요약(Daily, Weekly, Monthly)은 봇 내부 루프가 아닌 가벼운 Windows Task Scheduler 기반(`.bat`)으로 분리되어 메인 디스코드 봇은 응답 대기에만 집중합니다.
- **High-Quality News Pipeline**: 뉴스는 다중 소스 수집 후 정규화/중복제거/이벤트 클러스터링을 거쳐 `news_events/news_articles`와 `news_archive/*.json`으로 저장됩니다. 10분 폴링 시 이전 성공 시점 기준 overlap 윈도우를 재조회하고, 백필 실행으로 늦게 색인된 기사까지 보정합니다.
- **Collection Hardening**: 뉴스 수집은 소스별 마지막 시도/성공/오류/건수를 `news_ingest_checkpoints`에 남기고, `0건 저장` 실행은 pipeline success checkpoint를 전진시키지 않습니다. 아카이브는 당일 latest 파일과 run별 timestamp 파일을 함께 남깁니다.
- **News Context Pack Layer**: 뉴스 처리는 그룹챗과 분리된 `NewsContextPackService`가 담당합니다. 최근 `news_events`, 관련 `news_articles`, `research_evidences`를 하나의 판단 패키지로 묶고, 품질 상태(`strong/usable/weak/empty`)와 `web_required`를 표시한 뒤 토론/RAG에 전달합니다.
- **Official Company Data First**: 미국 상장사는 SEC EDGAR submissions/companyfacts API, 한국 6자리 종목코드는 OpenDART API를 우선 사용합니다. yfinance/Yahoo Finance 데이터는 현재가·기술지표 보조용으로만 표시하고, 실제 투자 전 별도 확인이 필요하다는 경고를 Fact-Sheet에 포함합니다.
- **Event-driven Web Verification**: 단기 시그널은 DB 요약문을 그대로 신뢰하지 않고, 신규 이벤트 발생 시 웹검색 증거 패키지로 재검증한 결과(근거 수/도메인 다양성/방향성)로만 승인 대기를 생성합니다.
- **Hybrid RAG Layer**: 토론 파이프라인(Phase 0)에 RAG가 결합되어 과거 실패/성공 사례를 이번 토론의 바탕(Base Argument)으로 씁니다.
- **Anti-Hallucination**: 외부 API 장애(529 Overloaded 등) 발생 시 안전한 Retry 로직을 갖추었고, 토론은 내부 사고 노출 없이 근거 중심 출력으로 제한됩니다. 또한 리서치 결과는 Evidence 패키지 형태로 저장되어 재검증이 가능합니다.
- **Evidence-ID Enforcement**: 최종 변론에는 `[근거ID: EVxxxx]` 태그를 강제해 근거 없는 결론 출력을 줄입니다.
- **Targeted Rebuttal**: `[조준:Model]` 태그로 즉각 방어 라운드를 실행하고, `[ACK]` 감지 시 불필요한 반복 루프를 단축합니다.
- **Gemini Auto Fallback**: `gemini-3.1-pro-preview`를 우선 호출하고, 타임아웃/과부하/비정상 응답 시 자동으로 `gemini-3-flash-preview`로 폴백합니다.
- **Degraded Mode**: 로컬 모델(Ollama) 장애 시 시스템은 강등 모드로 전환되어 규칙 기반 판정/기본 리서치를 유지하며, 장애 상태를 채널에 명확히 안내합니다.
- **Approval-based Signal Trading**: 뉴스 이벤트는 시그널로 점수화되며, 사용자 승인 전에는 주문이 실행되지 않습니다. 승인 시 기본은 페이퍼 체결입니다.
- **Human Final Decision Mode**: 현재 기본 운영 원칙은 최종 투자 판단 100% 수동입니다. 자동화는 뉴스 수집, 근거 정리, 토론 큐, 시그널, paper/replay 검증을 보조하며 실거래 브로커 연동은 비활성/미착수 단계입니다.
- **Debate Queue Automation**: 시그널 경로는 점수/긴급도/소스 tier/hidden candidate를 기준으로 `debate_queue`를 생성하고, 별도 배치가 이를 소비해 자동 토론을 실행할 수 있습니다.
- **Investment Review Triggers**: 포트폴리오 직접 연관과 hidden candidate가 검증 조건을 만족하면 `add_review`, `reduce_review`, `exit_review`, `hedge_review` 트리거를 저장합니다.
- **Fail-safe Paper Execution**: 가격 조회에 실패하면 `$1` 대체 체결을 하지 않고 실행을 중단합니다.
- **Paper Broker State**: 페이퍼 체결은 단순 로그가 아니라 계좌 현금, 포지션 평균단가, realized/unrealized PnL, 주문/체결 이력까지 갱신합니다.
- **Replay And Performance Tracking**: 실행 또는 시그널 기준 entry를 바탕으로 `15m/1h/1d/3d` 성과와 benchmark alpha를 기록할 수 있습니다.
- **Performance Feedback Loop**: replay 결과를 source tier, signal score, 검증 상태, 토론 품질, hidden candidate bucket별 attribution으로 묶어 다음 운영 정책의 강화/주의 후보를 만듭니다.
- **Data Quality Evaluation**: 수집 신선도, 커버리지, 공식/주요 출처 비중, 웹검증 커버리지, 데이터 관리 상태, 성과 측정 커버리지를 `data_quality.v1` 리포트로 점검합니다.
- **Audit Trails**: `event_intake_audits`, `context_selection_audits`가 이벤트 라우팅 이유와 AI 컨텍스트 선택 이유를 남겨, 판단 실패가 모델 문제인지 입력 컨텍스트 문제인지 복기할 수 있게 합니다.
- **Out-of-sample Replay**: `run_replay.bat`와 `REPLAY_SPLIT_DATE`를 사용해 train/test 구간을 나눈 replay와 MDD/equity curve를 저장할 수 있습니다.
- **Execution Realism**: paper execution은 기본 slippage/spread/urgency penalty를 반영하고, replay는 stop rule/TTL 기반 조기 청산을 반영합니다.
- **Ontology-Aware Planning**: 토론 시작 전에 온톨로지 플래너가 엔티티 링크/관계 확장 기반으로 `tickers`, `web_queries`, `rag_keywords`를 구성합니다.
- **Hidden Candidate Discovery**: 온톨로지는 제품/활동/매크로 이벤트를 경유하는 2~3-hop commonsense 경로로 숨은 관련 종목 후보를 제안합니다.
- **Validated Hidden Candidates**: hidden candidate는 path score 외에 validation score/flag를 같이 계산해 약한 연결을 억제합니다.
- **Ontology Auto-Relation Mining**: SEARCH evidence에서 관계(`supplies_to`, `competes_with` 등)를 자동 추출해 온톨로지를 갱신합니다.

## 🔐 보안 주의
- 세션 쿠키 파일(`src/data_fetcher/cookies.local.json`)은 개인 로컬에만 두고 절대 커밋하지 마세요.
- 예시 포맷은 `src/data_fetcher/cookies.local.example.json`를 참고하세요.

## ⚙️ 설치 메모
- 의존성 설치: `pip install -r requirements.txt`
- Playwright 브라우저 설치(필요 시): `playwright install`
- 로컬 모델 런타임 가이드: `LOCAL_MODEL_RUNTIME_GUIDE_2026-04-05.md`
- 윈도우 데스크탑 자동화 가이드: `WINDOWS_DESKTOP_AUTOMATION_GUIDE.md`
- 속도전 모드(선택): `DEBATE_SPEED_MODE=first_completed`
- 리서치 캐시(선택): `RESEARCH_CACHE_TTL_HOURS=12`
- 웹 fetch 병렬수(선택): `WEB_FETCH_CONCURRENCY=4`
- LLM 회로 차단기(선택): `CIRCUIT_FAILURE_THRESHOLD=3`, `CIRCUIT_COOLDOWN_SEC=60`
- 뉴스 품질 파라미터(선택): `NEWS_MAX_PER_SOURCE`, `NEWS_MAX_EVENTS`, `NEWS_LOOKBACK_HOURS`, `NYT_RATE_LIMIT_SECONDS`, `NEWS_REQUEST_TIMEOUT_SEC`
- 10분 폴링/백필 파라미터(선택): `NEWS_POLL_OVERLAP_MIN`, `NEWS_BACKFILL_HOURS`
- 뉴스 Context Pack 수동 쿼리(선택): `NEWS_CONTEXT_QUERIES` (`;` 구분)
- 공식 기업 데이터(권장): `SEC_USER_AGENT`, `SEC_REQUEST_TIMEOUT_SEC`, `DART_API_KEY`, `DART_REQUEST_TIMEOUT_SEC`
- 시그널 파라미터(선택): `SIGNAL_MIN_SCORE`, `SIGNAL_MAX_EVENTS`, `SIGNAL_VERIFY_NEW_ONLY`, `SIGNAL_VERIFY_BUDGET`, `SIGNAL_RECENCY_HOURS`
- 자동 토론/리뷰 파라미터(선택): `DEBATE_FRONTIER_MODE`, `DEBATE_AUTO_MIN_SCORE`, `DEBATE_REVIEW_MIN_SCORE`, `DEBATE_REQUIRE_VERIFIED`, `DEBATE_AUTO_COOLDOWN_MIN`, `REVIEW_TRIGGER_MIN_SCORE`, `DEBATE_JOB_MAX_ITEMS`
- 자동 토론 Discord 노출(선택): `DISCORD_DEBATE_WEBHOOK_URL`, `DISCORD_DEBATE_WEBHOOK_CHUNK_CHARS`, `DISCORD_DEBATE_LOG_CHARS`
- 시장 데이터 provider(현재 reference 용도): `MARKET_DATA_PROVIDER=yfinance`
- 페이퍼/리스크 파라미터(선택): `PAPER_STARTING_CASH`, `PAPER_COMMISSION_PER_ORDER`, `ALLOW_PAPER_SHORTS`, `RISK_DEFAULT_POSITION_PCT`, `RISK_MAX_TICKER_EXPOSURE_PCT`, `RISK_MAX_GROSS_EXPOSURE_PCT`, `RISK_MAX_OPEN_POSITIONS`
- execution realism 파라미터(선택): `PAPER_SLIPPAGE_BPS`, `PAPER_SPREAD_BPS`, `PAPER_IMMEDIATE_URGENCY_BPS`, `PAPER_HIGH_VOL_MULTIPLIER`
- 추가 리스크 파라미터(선택): `RISK_TICKER_COOLDOWN_MIN`, `RISK_HIGH_VOL_THRESHOLD_PCT`, `RISK_HIGH_VOL_SIZE_MULTIPLIER`
- 리플레이 파라미터(선택): `REPLAY_LIMIT`, `REPLAY_EVENT_ID`, `REPLAY_SPLIT_DATE`, `REPLAY_HORIZON`, `REPLAY_RUN_NAME`
- 온톨로지 필터(선택): `ONTOLOGY_HIDDEN_MIN_SCORE`

## 🕒 Windows 스케줄러 권장
- `run_news.bat`: 10분마다 실행(실시간 수집 + overlap 재조회)
- `run_news_context.bat`: 30분마다 또는 수동 실행(DB에 쌓인 뉴스/웹검증 근거를 독립 Context Pack으로 정리)
- `run_news_backfill.bat`: 하루 1회 실행(기본 최근 48시간 백필 보정)
- `run_signals.bat`: 10분~30분마다 실행(승인 대기 시그널 생성)
- `run_debates.bat`: 이벤트 기반 또는 10분~15분마다 실행(자동 토론 큐 소비)
- `run_replay.bat`: 필요 시 실행(최근 실행/시그널 성과 재측정)
  - `REPLAY_SPLIT_DATE=2026-01-01`를 주면 train/test 분리 replay 실행
- `run_data_quality.bat`: 필요 시 실행(수집/검증/뉴스팩/replay 커버리지 품질 점검)
- `run_local_healthcheck.bat`: 로컬 모델 프로파일(`json/extract/claim_search/evidence/evidence_verdict/judge`) 품질/지연 점검
- `run_maintenance.bat`: 매일 실행(DB 단기 캐시/뉴스팩/시그널 로그 보존기간 정리, `RETENTION_DAYS`)
- `run_bot.bat`: 디스코드 봇 실행(윈도우 로그온 자동 실행 옵션과 함께 사용 가능)
- 작업 스케줄러 자동 등록: `powershell -File scripts\windows\install_scheduled_tasks.ps1`

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
- 공개 온톨로지 + commonsense 확장 설계: `PUBLIC_ONTOLOGY_AND_COMMONSENSE_PLAN.md`
- 부트스트랩 로더: `python src/ontology_bootstrap.py --help`

## 📘 문서 인덱스
- 토론 동작 상세: `docs_debate_process.md`
- DB 스키마/저장 정책: `data_schema_and_tracking_guide.md`
- 기능/스택/포트폴리오 사용 가이드: `FEATURES_AND_PORTFOLIO_GUIDE.md`
- 승인형 단기 실행 설계: `승인형_이슈연동_트레이딩_실행_설계서.md`
- 장기투자 모델 계획: `장기투자_모델_계획서.md`
- 최신 변경 이력: `SYSTEM_UPDATE_NOTES_2026-03-03.md`
- 하드닝 상세 기록: `SYSTEM_HARDENING_NOTES_2026-04-05.md`
- 공개 온톨로지 + commonsense 설계: `PUBLIC_ONTOLOGY_AND_COMMONSENSE_PLAN.md`
- 로컬 모델 런타임 가이드: `LOCAL_MODEL_RUNTIME_GUIDE_2026-04-05.md`
- 투자 프로세스 감사 + 브로커 로드맵: `INVESTMENT_PROCESS_AUDIT_AND_BROKER_ROADMAP_2026-04-05.md`
- 페이퍼 실행 + 리플레이 구현 기록: `PAPER_EXECUTION_AND_REPLAY_IMPLEMENTATION_2026-04-05.md`
- 전체 로드맵 + 현재 상태 기준 문서: `MASTER_SYSTEM_ROADMAP_AND_STATUS_2026-04-05.md`
- 자동화 정책 + 토론/투자 트리거 기준: `AUTOMATION_POLICY_AND_TRIGGER_RULES_2026-04-05.md`
- 윈도우 데스크탑 자동화 가이드: `WINDOWS_DESKTOP_AUTOMATION_GUIDE.md`
- 2026-05-05 구현/문서 정리 기록: `IMPLEMENTATION_RECORD_2026-05-05.md`
- 재가동 가이드: `System Restart Guide`
## 2026-05-24 Operating Notes

- Local LLM routing now supports per-profile model overrides such as `LOCAL_MODEL_NAME_EVIDENCE_VERDICT` and `LOCAL_MODEL_NAME_JUDGE`. Recommended production posture is hybrid: e4b for fast/high-volume helper work, 31B for evidence verdicts, judge-style arbitration, and long evidence summaries.
- Market data currently works as reference data through yfinance/Yahoo Finance via `MarketDataProvider`. It is useful for paper execution, portfolio PnL, replay, and basic price context, but it is not yet execution-grade live quote/tick data.
- Market data is now market-aware for common US, Korean, Japanese, Hong Kong, China, UK, Canada, Australia, India, and other Yahoo Finance suffix formats. See `MARKET_DATA_CONNECTIVITY_2026-05-24.md`; run `run_market_data_check.bat` to verify current connectivity.
- For real investment use, news should be evaluated together with pre/post event price movement, volume, sector/benchmark relative return, and whether the move happened before the news reached public feeds. That market-reaction context is a next hardening priority.
- Before sandbox/live trading, use `LIVE_TRADING_READINESS_2026-05-24.md` as the go/no-go checklist.
- New local hardening jobs: `run_market_reaction.bat`, `run_reconciliation.bat`, and `run_live_readiness_check.bat`.
- Set `DISCORD_OPERATOR_USER_IDS` to restrict approval, debate queue mutation, and kill-switch commands to specific Discord user IDs.
- `SIGNAL_CAPTURE_MARKET_REACTION=true` can capture market reaction snapshots during signal generation; leave it off for faster routine runs.
