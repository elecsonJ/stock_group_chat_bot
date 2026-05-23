# Local Model Runtime Guide (2026-04-05)

## 목적
로컬 모델은 프론티어 모델의 완전 대체재가 아니라, 다음 역할에 특화된 좁은 판정기로 사용합니다.

- 검색어 추출
- JSON 분류/정규화
- Evidence 요약
- News Context Pack limitations 해석
- RAG 답변 합성
- 최종 판정 보조

즉 로컬 모델에 너무 많은 권한과 너무 많은 컨텍스트를 주지 않고,
짧은 입력에서 일관성 있게 동작하도록 만드는 것이 목적입니다.

## 현실적 모델 기준

### 현재 권장
- Gemma 3 27B 계열
- QAT 4bit / GGUF 계열
- LM Studio 또는 Ollama 사용

### 중요한 현실 체크
- 공식 Google Gemma 최신 공개 계열은 현재 `Gemma 3`입니다.
- `Gemma 4 31B`라는 공식 공개 모델은 이 작업 시점에 확인하지 못했습니다.
- 따라서 실제 도입 기준은 `Gemma 3 27B` 또는 그 QAT/GGUF 변형으로 잡는 것이 안전합니다.

## 하드웨어 관점

### 사용자 환경
- VRAM: 16GB
- RAM: 48GB

### 의미
- BF16/FP16 27B는 사실상 무겁습니다.
- Q4 GGUF + CPU offload 또는 mixed offload가 현실적입니다.
- 속도보다 "좁은 작업을 정확히 수행"하게 설계해야 합니다.

## 권장 운용 전략

### 로컬 모델에 맡길 일
- `extract`
  - 키워드 추출
  - 검색어 정리
  - JSON 분류
- `evidence`
  - 검색 결과 요약
  - 짧은 상충 근거 정리
- `news_context`
  - 이미 구조화된 뉴스팩의 한계/보완 필요성 해석
  - 원문 전체 독해가 아니라 짧은 패키지 기반 판단 보조
- `judge`
  - 토론 결과의 규칙 기반 보조 판정
  - 합의 여부/최종 요지 정리
- `rag_answer`
  - 이미 축약된 근거를 읽고 답변 합성

### 로컬 모델에 맡기지 말아야 할 일
- 대규모 원문 뉴스 덩어리 전체 정독
- 로컬에 쌓인 뉴스만으로 최신 사실을 단정
- 장문 회의록 무제한 판독
- 자유형 탐색과 결론을 동시에 수행하는 작업
- 근거가 빈약한 상태에서 최종 투자 결론 단독 결정

## 이번 코드 변경점

### 1. 로컬 백엔드 선택 가능
`src/llm_client.py`

- `LOCAL_MODEL_BACKEND=ollama`
- `LOCAL_MODEL_BACKEND=openai_compatible`

둘 중 하나를 선택할 수 있습니다.

### 2. LM Studio 지원
`openai_compatible` 모드에서는 LM Studio의 OpenAI-compatible endpoint를 사용합니다.

필수 env:
- `LOCAL_MODEL_NAME`
- `LOCAL_OPENAI_BASE_URL`
- `LOCAL_API_KEY`

예시:
```env
LOCAL_MODEL_BACKEND=openai_compatible
LOCAL_MODEL_NAME=gemma-3-27b-it
LOCAL_OPENAI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_API_KEY=lm-studio
```

### 3. 역할별 컨텍스트 예산
코드에서 로컬 모델 호출은 다음 프로파일을 사용합니다.

- `json`
- `extract`
- `summary`
- `rag_answer`
- `judge`
- `evidence`

환경변수:
- `LOCAL_CONTEXT_BUDGET_DEFAULT`
- `LOCAL_CONTEXT_BUDGET_JSON`
- `LOCAL_CONTEXT_BUDGET_EXTRACT`
- `LOCAL_CONTEXT_BUDGET_SUMMARY`
- `LOCAL_CONTEXT_BUDGET_RAG`
- `LOCAL_CONTEXT_BUDGET_JUDGE`
- `LOCAL_CONTEXT_BUDGET_EVIDENCE`
- `LOCAL_CONTEXT_BUDGET_CLAIM_SEARCH`
- `LOCAL_CONTEXT_BUDGET_EVIDENCE_VERDICT`

로컬 모델은 이제 단순 요약뿐 아니라 `claim_search`와 `evidence_verdict` 역할도 맡습니다. `claim_search`는 하나의 투자 주장이나 뉴스 이벤트를 검증 가능한 사실 주장과 여러 검색어로 나누고, `evidence_verdict`는 수집된 근거 패키지를 보고 토론/신호에 넘겨도 되는지 보수적으로 판정합니다.

긴 입력은 가운데를 잘라내는 방식으로 자동 축약됩니다.

## 추천 설정

### LM Studio + Gemma 3 27B QAT/GGUF
```env
LOCAL_MODEL_BACKEND=openai_compatible
LOCAL_MODEL_NAME=gemma-3-27b-it
LOCAL_OPENAI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_API_KEY=lm-studio
LOCAL_TIMEOUT_SEC=600
LOCAL_MAX_OUTPUT_TOKENS=1536

LOCAL_CONTEXT_BUDGET_DEFAULT=8000
LOCAL_CONTEXT_BUDGET_JSON=1800
LOCAL_CONTEXT_BUDGET_EXTRACT=2200
LOCAL_CONTEXT_BUDGET_SUMMARY=10000
LOCAL_CONTEXT_BUDGET_RAG=12000
LOCAL_CONTEXT_BUDGET_JUDGE=14000
LOCAL_CONTEXT_BUDGET_EVIDENCE=8000
LOCAL_CONTEXT_BUDGET_CLAIM_SEARCH=6000
LOCAL_CONTEXT_BUDGET_EVIDENCE_VERDICT=14000
```

### Ollama 유지 시
```env
LOCAL_MODEL_BACKEND=ollama
LOCAL_MODEL_NAME=gpt-oss:20b
LOCAL_OLLAMA_URL=http://localhost:11434/api/chat
```

## 운영 원칙

### 로컬 모델 품질을 높이는 가장 중요한 요소
1. 입력 범위를 좁힌다.
2. 출력 형식을 강하게 제한한다.
3. 검색/판단/최종결론을 한 번에 시키지 않는다.
4. frontier 모델용 장문 히스토리를 그대로 로컬 모델에 주지 않는다.

### 권장 분업
- 온톨로지: 탐색 범위 확장
- 웹 검증: 근거 확보
- 로컬 모델: 구조화/요약/판정
- 프론티어 모델: 장문 논증과 최종 토론

## 체크리스트
- LM Studio 서버가 떠 있는가
- OpenAI-compatible endpoint가 켜져 있는가
- 선택한 Gemma 모델이 실제로 load 되었는가
- 로컬 모델이 JSON 출력 같은 좁은 작업에서 먼저 안정적인가
- 대형 입력이 budget에 의해 잘 축약되고 있는가

## LM Studio 연결 점검 순서
현재 코드의 기본값은 하위 호환 때문에 `LOCAL_MODEL_BACKEND=ollama`입니다. LM Studio를 쓰려면 `.env`에 아래 값을 명시해야 합니다.

```env
LOCAL_MODEL_BACKEND=openai_compatible
LOCAL_MODEL_NAME=gemma-3-27b-it
LOCAL_OPENAI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_API_KEY=lm-studio
```

점검 순서:

1. LM Studio에서 모델을 Load합니다.
2. Developer 또는 Local Server 메뉴에서 OpenAI-compatible server를 켭니다.
3. 기본 포트가 `1234`인지 확인합니다.
4. PowerShell에서 포트 확인:

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 1234
```

5. 프로젝트에서 healthcheck 실행:

```powershell
.\run_local_healthcheck.bat
```

정상이라면 healthcheck 출력에 `backend=openai_compatible`, `base_url=http://127.0.0.1:1234/v1`, `endpoint=OpenAI-compatible chat.completions`가 표시되고 각 profile 케이스가 `OK`로 나와야 합니다.

## 후속 제안
1. 로컬 모델용 health check 명령 추가
2. 모델별 latency/실패율 기록
3. local profile별 실제 평균 입력 길이 로그
4. Gemma 3 27B와 기존 gpt-oss:20b 비교 벤치 추가
## 31B/e4b Routing Policy (2026-05-24)

Stock debate is quality-sensitive: a small factual error can change the final decision. The recommended default is hybrid routing, not a single model for every local task.

- Fast/default tasks: keep `LOCAL_MODEL_NAME=google/gemma-4-e4b` for high-volume extraction, JSON formatting, and quick claim-to-search work.
- Quality-critical tasks: set `LOCAL_MODEL_NAME_EVIDENCE_VERDICT=google/gemma-4-31b` and `LOCAL_MODEL_NAME_JUDGE=google/gemma-4-31b`.
- Long evidence summaries: set `LOCAL_MODEL_NAME_EVIDENCE=google/gemma-4-31b` when latency is acceptable.
- Deep/nightly runs: if delay is acceptable, set `LOCAL_MODEL_NAME=google/gemma-4-31b` so most local work uses 31B.

Operational rule: use e4b when freshness and throughput matter, use 31B when the output decides whether evidence is trustworthy enough for debate, signal scoring, or final judgment.

```env
LOCAL_MODEL_NAME=google/gemma-4-e4b
LOCAL_MODEL_NAME_EVIDENCE_VERDICT=google/gemma-4-31b
LOCAL_MODEL_NAME_JUDGE=google/gemma-4-31b
LOCAL_MODEL_NAME_EVIDENCE=google/gemma-4-31b
```
