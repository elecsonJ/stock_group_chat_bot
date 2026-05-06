# System Update Notes (2026-03-03)

## 핵심 변경
1. JSON 파싱 안정화(`json_utils.py`)
2. 판정 로직 강화(규칙 기반 우선 + JSON 복구 재시도)
3. Evidence 패키지 도입 및 DB 저장(`research_evidences`)
4. SEARCH 중복 제거/루프 조기 종료
5. 강제 SEARCH 근거 확보(결론 전 최소 1회)
6. 로컬 모델 장애 강등 모드
7. DB WAL/인덱스/보존 정책 추가
8. 수치 이상치 경고(배당/PER/기관보유율/공매도)
9. 배치 파일 경로 안정화(`%~dp0`)
10. 쿠키 보안 정리(실쿠키 제거, 로컬 예시 파일로 전환)
11. ACK/조준 태그 기반 토론 제어 추가(`src/debate_manager.py`)
12. 최종 결론 근거ID 강제 태그(`[근거ID: EVxxxx]`)
13. RAG FTS 인덱스 도입(FTS5 + LIKE fallback)
14. SEARCH evidence 기반 온톨로지 관계 자동추출기 추가
15. 뉴스 스크래핑 고도화: 다중 소스 수집 + 정규화 + 이벤트 클러스터링 + DB 구조화 저장
16. 뉴스 폴링 강화: 10분 overlap 윈도우 + 일간 backfill + 체크포인트/ingest delay 추적
17. 승인형 단기 시그널 트레이딩: 이벤트 점수화 + 승인/거부 + 페이퍼 체결 + 가드레일 명령 추가
18. 단기 시그널 웹검증 강화: 신규 이벤트에 한해 웹검색 증거 패키지 검증 후에만 승인 대기 생성

## 온톨로지 통합
1. `src/ontology/store.py`: 엔티티/별칭/관계 저장소
2. `src/ontology/planner.py`: 온톨로지 기반 리서치 플랜 생성
3. `src/ontology_bootstrap.py`: SEC/DART-KRX/LEI/FIGI ingest
4. `src/debate_manager.py`: 온톨로지 플랜을 Fact-Sheet 구성에 병합
5. `src/ontology/relation_miner.py`: evidence 텍스트에서 관계 자동추출
6. `src/data_fetcher/premium_crawler.py`: 고품질 뉴스 수집/클러스터링 파이프라인

## 문서 정리
1. 런타임 스펙: `docs_debate_process.md`
2. 데이터 스키마: `data_schema_and_tracking_guide.md`
3. 온톨로지 워크플로우: `ONTOLOGY_RAG_WEB_WORKFLOW.md`
4. 실험/미구현 목록: `workflow_advanced_quant_agent.md`
5. 재가동 가이드: `System Restart Guide`

## 추가 하드닝 메모 (2026-04-05)
1. `src/evidence_memory.py` 추가: 최근 뉴스 이벤트 + 웹검증 evidence를 토론/RAG 메모리로 통합
2. `src/debate_manager.py` 개선: 뉴스 원문 덩어리 대신 구조화 이벤트 브리프 입력 사용(2026-05-05 후속 작업에서 News Context Pack으로 승격)
3. `src/rag_agent.py` 개선: debates/summaries 외에 최근 `news_events`/`research_evidences`도 참조
4. `src/db_manager.py` 개선: 테스트용 `db_path` 지원, FTS rebuild 1회화, 상태 동기화 메서드 추가
5. `src/signal_engine.py` 개선: 비실행가능 이벤트 자동 supersede 처리
6. `src/trading_executor.py` 개선: 가격 조회 실패 시 fail-closed
7. `src/web_search_agent.py` 개선: 출처 품질 정렬, URL 정규화, 중복 제거
8. `src/summarizer.py` 개선: 뉴스 이벤트 + 토론 결론 결합 일간 요약
9. `tests/` 추가: 시그널 상태, EvidenceMemory, TradingExecutor 회귀 테스트
10. 상세 기록 문서: `SYSTEM_HARDENING_NOTES_2026-04-05.md`
11. commonsense ontology 확장:
    - `src/ontology/store.py` 2-hop hidden candidate 탐색
    - `src/ontology_bootstrap.py --commonsense-json`
    - `commonsense_ontology.example.json`
    - `PUBLIC_ONTOLOGY_AND_COMMONSENSE_PLAN.md`
# 2026-04-05 추가 업데이트

## 페이퍼 실행/리플레이 계층 추가
- `src/paper_broker.py`
- `src/risk_manager.py`
- `src/performance_tracker.py`
- `src/replay_engine.py`
- `src/replay_job.py`
- `run_replay.bat`

핵심 변화:
- 승인형 실행이 `order_executions` 로그만 남기던 구조에서
  `paper_account_state`, `paper_positions`, `paper_orders`, `paper_fills`를 함께 갱신하는 구조로 변경
- 실행 전 리스크 검사를 수행해 종목/총노출/현금 부족을 차단
- 과거 실행/시그널에 대해 horizon별 수익률과 alpha를 측정할 수 있는 `signal_performance` 계층 추가
- split replay, equity curve, MDD, attribution, execution realism, stop/TTL exit 지원 추가

# 2026-05-05 추가 업데이트

## News Context Pack 독립화
- `src/news_context_pack.py`
- `src/news_context_job.py`
- `run_news_context.bat`

핵심 변화:
- 뉴스 처리를 그룹챗/토론 모델과 분리
- `news_events`, `news_articles`, `research_evidences`를 하나의 판단 패키지로 구성
- `strong/usable/weak/empty`, `web_required`, `limitations`, 권장 웹검증 쿼리를 함께 저장
- `news_context_packs` 테이블에 생성 결과 저장
- 토론/RAG는 원자료 DB가 아니라 `NewsContextPackService`의 출력만 받아 사용
- Windows 작업 스케줄러에 `StockBot\NewsContextPack` 30분 주기 작업 추가

## 운영 정책 명확화
- 현재 기본 모드는 최종 투자 판단 100% 수동
- 자동화 범위는 뉴스 수집, 뉴스팩 정리, 시그널, 토론 큐, paper/replay 검증까지
- 실거래 브로커 연동과 자동 주문은 아직 기본 운영 범위가 아님
- 상세 기록: `IMPLEMENTATION_RECORD_2026-05-05.md`
