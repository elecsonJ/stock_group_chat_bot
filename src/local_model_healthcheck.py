import asyncio
import json
import os
from time import monotonic

from llm_client import LLMClientManager
from json_utils import parse_json_object


CASES = [
    {
        "name": "json_classify",
        "profile": "json",
        "system": "JSON 출력기",
        "prompt": (
            '다음 텍스트를 읽고 정확히 JSON만 출력해. '
            '{"is_recent_issue": true|false, "tickers": ["..."]}\n'
            "텍스트: NVIDIA가 AI 서버 수요 둔화 우려에도 중동향 대형 수주를 따냈다."
        ),
        "expect_json": True,
    },
    {
        "name": "extract_queries",
        "profile": "extract",
        "system": "너는 검색어 추출기야.",
        "prompt": (
            "다음 문장에서 검색에 쓸 핵심 키워드 1개만 뽑아.\n"
            "문장: 테슬라의 로보택시 규제 승인 가능성과 수익성 영향이 궁금해."
        ),
        "expect_json": False,
    },
    {
        "name": "evidence_summary",
        "profile": "evidence",
        "system": (
            "너는 수석 리서처야. 아래 JSON evidence만 근거로 3개 이내 불릿으로 요약하고, "
            "마지막 줄에 검증 상태를 적어."
        ),
        "prompt": json.dumps(
            {
                "query": "NVIDIA middle east AI server contract",
                "evidences": [
                    {
                        "evidence_id": "E1",
                        "title": "NVIDIA wins AI server order",
                        "domain": "reuters.com",
                        "snippet": "NVIDIA secured a multi-year AI server supply agreement.",
                    },
                    {
                        "evidence_id": "E2",
                        "title": "Company discloses new regional partnership",
                        "domain": "investor.nvidia.com",
                        "snippet": "The company announced expanded regional infrastructure partnerships.",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "expect_json": False,
    },
    {
        "name": "judge_verdict",
        "profile": "judge",
        "system": (
            "너는 투자 토론 판정기야. 아래 찬반 근거를 읽고 최종 결론을 4문장 이내로 쓰고, "
            "마지막에 bullish/bearish/neutral 중 하나를 적어."
        ),
        "prompt": (
            "[찬성] 공급계약, 가이던스 상향, 데이터센터 수요.\n"
            "[반대] 밸류에이션 부담, CAPEX 둔화 우려, 규제 리스크."
        ),
        "expect_json": False,
    },
    {
        "name": "claim_to_search",
        "profile": "claim_search",
        "system": (
            "You are a claim-to-search planner. Return only strict JSON with claims, "
            "search_queries, priority, required_sources, and reason."
        ),
        "prompt": (
            "Claim: NVIDIA announced a new Middle East AI infrastructure contract that may affect 2026 revenue.\n"
            "Return at most 3 search queries and do not make an investment conclusion."
        ),
        "expect_json": True,
        "expect_keys": ["claims", "search_queries", "priority", "required_sources", "reason"],
    },
    {
        "name": "evidence_verdict",
        "profile": "evidence_verdict",
        "system": (
            "You are an evidence verdict worker. Use only provided evidence. "
            "Return only strict JSON using exactly the requested keys."
        ),
        "prompt": (
            "[Evidence]\n"
            + json.dumps(
                {
                    "query": "NVIDIA Middle East AI infrastructure contract",
                    "evidences": [
                        {
                            "evidence_id": "E1",
                            "domain": "reuters.com",
                            "source_tier": "tier1_media",
                            "snippet": "NVIDIA announced a regional AI infrastructure partnership.",
                        },
                        {
                            "evidence_id": "E2",
                            "domain": "investor.nvidia.com",
                            "source_tier": "company_ir",
                            "snippet": "The company disclosed expanded regional infrastructure partnerships.",
                        },
                    ],
                },
                ensure_ascii=False,
            )
            + "\n\n[Required JSON]\n"
            + json.dumps(
                {
                    "status": "strong|usable|insufficient|contradictory",
                    "ready_for_debate": True,
                    "ready_for_signal": False,
                    "verified_claims": [],
                    "partially_supported_claims": [],
                    "unsupported_claims": [],
                    "contradictions": [],
                    "best_sources": [],
                    "missing_evidence": [],
                    "recommended_next_searches": [],
                    "allowed_use": "debate_and_signal|discussion_only|needs_refresh",
                    "confidence": 0.0,
                },
                ensure_ascii=False,
            )
        ),
        "expect_json": True,
        "expect_keys": [
            "status",
            "ready_for_debate",
            "ready_for_signal",
            "verified_claims",
            "missing_evidence",
            "recommended_next_searches",
            "confidence",
        ],
    },
]


def _safe_preview(text: str, limit: int = 160) -> str:
    raw = str(text or "").replace("\n", " ").strip()
    return raw[:limit] + ("..." if len(raw) > limit else "")


async def _run_case(manager: LLMClientManager, case: dict[str, object]) -> dict[str, object]:
    started = monotonic()
    try:
        response = await manager.get_local_response(
            str(case["system"]),
            str(case["prompt"]),
            profile=str(case["profile"]),
        )
        latency = round(monotonic() - started, 3)
        parsed_json = None
        is_json = False
        missing_keys: list[str] = []
        if case.get("expect_json"):
            parsed_json = parse_json_object(response)
            is_json = parsed_json is not None
            expect_keys = [str(k) for k in case.get("expect_keys", [])] if isinstance(case.get("expect_keys", []), list) else []
            if expect_keys and isinstance(parsed_json, dict):
                missing_keys = [key for key in expect_keys if key not in parsed_json]
            elif expect_keys:
                missing_keys = expect_keys
        ok = is_json if case.get("expect_json") else bool(str(response or "").strip())
        if missing_keys:
            ok = False
        return {
            "name": case["name"],
            "profile": case["profile"],
            "model": manager.local_model_for_profile(str(case["profile"])),
            "ok": ok,
            "latency_sec": latency,
            "response_chars": len(str(response or "")),
            "preview": _safe_preview(response),
            "json_valid": is_json if case.get("expect_json") else None,
            "missing_keys": missing_keys,
            "error": "",
        }
    except Exception as exc:
        latency = round(monotonic() - started, 3)
        return {
            "name": case["name"],
            "profile": case["profile"],
            "model": manager.local_model_for_profile(str(case["profile"])),
            "ok": False,
            "latency_sec": latency,
            "response_chars": 0,
            "preview": "",
            "json_valid": False if case.get("expect_json") else None,
            "missing_keys": [],
            "error": str(exc),
        }


async def main():
    manager = LLMClientManager()
    repeat = max(1, int(os.getenv("LOCAL_HEALTHCHECK_REPEAT", "1")))
    case_name = os.getenv("LOCAL_HEALTHCHECK_CASE", "").strip().lower()
    selected = [
        case for case in CASES
        if not case_name or str(case["name"]).lower() == case_name
    ]
    if not selected:
        raise SystemExit(f"unknown LOCAL_HEALTHCHECK_CASE: {case_name}")

    print("=== local model healthcheck ===")
    print(f"backend={manager.local_backend}")
    print(f"model={manager.models['local']}")
    if manager.local_backend == "openai_compatible":
        print(f"base_url={manager.local_openai_base_url}")
        print("endpoint=OpenAI-compatible chat.completions")
    else:
        print(f"ollama_url={manager.local_ollama_url}")
    print(f"repeat={repeat}")
    print(f"cases={', '.join(str(c['name']) for c in selected)}")

    results: list[dict[str, object]] = []
    for _ in range(repeat):
        for case in selected:
            result = await _run_case(manager, case)
            results.append(result)
            status = "OK" if result["ok"] else "FAIL"
            print(
                f"[{status}] {result['name']} profile={result['profile']} "
                f"model={result['model']} latency={result['latency_sec']}s chars={result['response_chars']}"
            )
            if result["error"]:
                print(f"  error={result['error']}")
            if result["preview"]:
                print(f"  preview={result['preview']}")

    failed = sum(1 for item in results if not item["ok"])
    avg_latency = round(
        sum(float(item["latency_sec"]) for item in results) / len(results),
        3,
    ) if results else 0.0
    print("--- summary ---")
    print(f"total={len(results)} failed={failed} avg_latency_sec={avg_latency}")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
