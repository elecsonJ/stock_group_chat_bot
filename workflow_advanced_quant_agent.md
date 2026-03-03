# Advanced Workflow Notes (Implemented vs Experimental)

이 문서는 아이디어 문서입니다. 아래 상태를 기준으로 해석하세요.

## 구현됨 (Implemented)
1. 블라인드 1R + 교차 반박 라운드
2. `[SEARCH: ...]` 기반 심층 리서치
3. Evidence 패키지 저장(`research_evidences`)
4. 규칙 기반 판정 + 로컬 판사 보조
5. 로컬 모델 장애 시 강등 모드
6. 온톨로지 플래너 기반 ticker/search 확장
7. `[ACK]` 태그 감지 기반 조기 종료
8. `[조준:Model]` 즉각 방어권
9. 속도전 `DEBATE_SPEED_MODE=first_completed` (Loop 1)
10. 최종 결론의 `[근거ID: ...]` 강제 표기

## 미구현/실험 (Experimental)
1. 모델 내부 사고(CoT) 공유형 토론

## 권장
운영 문서는 `docs_debate_process.md`와 `ONTOLOGY_RAG_WEB_WORKFLOW.md`를 기준으로 유지하세요.
