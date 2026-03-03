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
