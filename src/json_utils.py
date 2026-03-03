import json
import re
from typing import Any, Optional


def strip_code_fences(text: str) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def extract_first_balanced_json(text: str) -> Optional[str]:
    if not text:
        return None

    cleaned = strip_code_fences(text)
    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]

        if escaped:
            escaped = False
            continue

        if ch == "\\":
            escaped = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : idx + 1]

    return None


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = strip_code_fences(text)

    # 1) 문자열 전체가 JSON인 경우
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # 2) 문장 중 JSON 블록만 포함된 경우
    candidate = extract_first_balanced_json(cleaned)
    if candidate:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def validate_unanimity_payload(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    if status == "만장일치":
        return isinstance(payload.get("conclusion"), str) and bool(payload.get("conclusion").strip())
    if status == "불합치":
        votes = payload.get("votes")
        if not isinstance(votes, dict):
            return False
        return all(k in votes for k in ("GPT", "Claude", "Gemini"))
    return False


def validate_final_verdict_payload(payload: dict[str, Any]) -> bool:
    required = ("status", "majority_choice", "logical_winner", "fatal_flaw")
    for key in required:
        val = payload.get(key)
        if not isinstance(val, str) or not val.strip():
            return False
    return True
