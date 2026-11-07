"""Why an AI CLI call failed, in words the operator can act on (fork 2026-11-07).

The work ledger (core.usage) used to keep only ERROR/TIMEOUT. This module turns a finished call's exit code,
timeout reason and output into a short reason code + Korean label + a trimmed detail line. Pure functions only.
"""
import json
import re
from typing import Any, Dict, Optional

# code -> (label shown to the user, what to do)
REASONS = {
    "USAGE_LIMIT": ("사용 한도 초과", "구독 사용 한도가 풀릴 때까지 기다리거나, 그 역할을 다른 AI로 바꾸세요."),
    "RATE_LIMIT": ("요청이 너무 잦음", "잠시 뒤 다시 시도하세요."),
    "CONTEXT_TOO_LONG": ("입력이 너무 김(토큰 한도)", "작업 지시를 작게 나누거나 참고 문서를 줄이세요."),
    "AUTH": ("로그인 필요", "터미널에서 해당 CLI에 다시 로그인하세요."),
    "OVERLOADED": ("AI 서버 과부하", "잠시 뒤 다시 시도하세요."),
    "NETWORK": ("네트워크 오류", "인터넷 연결을 확인하세요."),
    "NO_OUTPUT_TIMEOUT": ("출력 없이 시간 초과", "config.yaml timeouts.no_progress를 늘리거나 다른 AI로 바꾸세요."),
    "HARD_TIMEOUT": ("제한 시간 초과", "config.yaml timeouts의 역할별 시간을 늘리거나 작업을 나누세요."),
    "TIMEOUT": ("시간 초과", "config.yaml timeouts를 확인하세요."),
    "NOT_FOUND": ("CLI를 찾지 못함", "CLI가 설치돼 있고 PATH에 있는지 확인하세요."),
    "INTERRUPTED": ("중단됨(강제 종료)", "작업 정지·시스템 중지로 끊겼거나 프로세스가 강제 종료됐습니다."),
    "BAD_OUTPUT": ("응답 형식 오류", "AI가 약속한 JSON 대신 다른 내용을 돌려줬습니다."),
    "EXIT": ("CLI 오류 종료", "자세한 내용을 확인하세요."),
    "UNKNOWN": ("원인 기록 없음", "이 기능을 넣기 전의 호출이라 원인이 남아 있지 않습니다."),
}

_PATTERNS = [
    ("USAGE_LIMIT", r"hit your [\w\s-]*limit|usage[_ ]limit|limit_reached|quota (?:exceeded|exhausted)|resource_exhausted"
                    r"|exceeded your current quota|out of credits|credit balance is too low|weekly limit|5-hour limit"),
    ("CONTEXT_TOO_LONG", r"prompt is too long|context (?:length|window)|maximum context|too many tokens"
                         r"|input is too long|token limit exceeded"),
    ("AUTH", r"not logged in|please (?:log ?in|run .*login)|authenticat|unauthori[sz]ed|\b401\b|invalid api key"
             r"|oauth token|login required|credentials? (?:expired|invalid|not found)"),
    ("RATE_LIMIT", r"rate[_ ]limit|too many requests|\b429\b"),
    ("OVERLOADED", r"overloaded|\b529\b|\b503\b|service unavailable|server error|\b500\b internal"),
    ("NETWORK", r"enotfound|econnreset|econnrefused|etimedout|getaddrinfo|network (?:error|is unreachable)"
                r"|could not resolve host|connection (?:reset|refused)"),
    ("NOT_FOUND", r"command not found|no such file or directory|executable .*not found|filenotfounderror"),
    ("INTERRUPTED", r"error: interrupted|keyboardinterrupt|stopped by (?:the )?(?:user|operator)"),
]

_RESET = re.compile(r"resets?\s+(?:at\s+)?([0-9][0-9:apm\s]{0,10}?(?:\s*\([^)]{1,30}\))?)(?=[\s\".,]|$)", re.I)


def _envelope_text(stdout: str) -> str:
    """Claude/Codex JSON envelopes carry the real message in result/error fields."""
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts = [str(data.get(k) or "") for k in ("result", "error", "message", "api_error_status", "subtype")]
    return " ".join(p for p in parts if p)


def _first_line(text: str, limit: int = 300) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def classify(exit_code: Optional[int] = None, timed_out: bool = False, timeout_reason: Optional[str] = None,
             stdout: str = "", stderr: str = "", error: str = "") -> Dict[str, str]:
    """Return {code, label, hint, detail}. ``error`` is any extra message (exception text, attempt error)."""
    stdout, stderr, error = stdout or "", stderr or "", error or ""
    # "GOOGLE CLI exit -9: {stream...}": keep the message, drop the echoed NDJSON stream (tool lists etc.)
    brace = error.find("{")
    if brace > 0 and error[:brace].rstrip().endswith(":"):
        error = error[:brace].rstrip(" :")
    envelope = _envelope_text(stdout)
    haystack = "\n".join(x for x in (error, envelope, stderr) if x)
    low = haystack.lower()
    code = None
    for name, pattern in _PATTERNS:
        if re.search(pattern, low):
            code = name
            break
    if code is None and not envelope and stdout:
        # Raw stdout may be model output (code that mentions 429...), so only trust the unmistakable messages.
        tail = stdout[-1500:].lower()
        for name, pattern in _PATTERNS[:3]:
            if re.search(pattern, tail):
                code, haystack = name, haystack + "\n" + stdout[-1500:]
                break
    reason = str(timeout_reason or "")
    if code is None:
        if "NO_OUTPUT_TIMEOUT" in reason or "NO_OUTPUT_TIMEOUT" in error:
            code = "NO_OUTPUT_TIMEOUT"
        elif "HARD_TIMEOUT" in reason or "HARD_TIMEOUT" in error:
            code = "HARD_TIMEOUT"
        elif timed_out or "TIMEOUT" in error.upper():
            code = "TIMEOUT"
        elif exit_code in (-9, -15, 137, 143) or re.search(r"exit -(?:9|15)\b", error):
            code = "INTERRUPTED"
        elif "json" in low and ("decode" in low or "parse" in low or "invalid" in low):
            code = "BAD_OUTPUT"
        elif exit_code not in (None, 0) or haystack.strip():
            code = "EXIT"
        else:
            code = "UNKNOWN"
    label, hint = REASONS[code]
    detail = _first_line(envelope) or _first_line(error) or _first_line(stderr) or reason
    if code == "EXIT" and exit_code not in (None, 0):
        label = f"{label} (코드 {exit_code})"
    reset = _RESET.search(haystack)
    if code == "USAGE_LIMIT" and reset:
        label = f"{label} · {reset.group(1).strip()}에 풀림"
    return {"code": code, "label": label, "hint": hint, "detail": detail[:300]}


def describe(code: Optional[str]) -> Dict[str, str]:
    label, hint = REASONS.get(code or "UNKNOWN", REASONS["UNKNOWN"])
    return {"code": code or "UNKNOWN", "label": label, "hint": hint}


# ------------------------------------------------------------------ whole run steps (state_transitions)

STEP_REASONS = {
    "BUILDER_FAILED": ("구현 AI가 결과를 못 냄", "아래 원인을 확인하세요. 같은 원인이 반복되면 BUILDER를 다른 AI로 바꾸세요."),
    "REVIEW_REJECTED": ("검수·판정에서 불합격", "HEAD 판정 내용을 읽고 다음 단계에서 고치게 하거나 직접 고치세요."),
    "VERIFY_FAILED": ("빌드·테스트 실패", "작업실의 검증 출력에서 오류 줄을 확인하세요."),
    "NO_PROVIDER": ("쓸 수 있는 AI가 없음", "CLI 로그인·사용 한도를 확인하거나 역할을 바꾸세요."),
    "DIRECTOR_ERROR": ("Director 내부 오류", "작업본이 지워졌거나 프로그램 오류입니다. 미션을 정리하고 다시 시작하세요."),
    "USER_DECISION": ("사용자 결정 필요", "작업실에서 질문을 확인하고 답하세요."),
    "STEP_TIMEOUT": ("단계 시간 초과", "config.yaml timeouts를 늘리거나 작업을 나누세요."),
    "STEP_OTHER": ("단계 실패", "자세한 내용을 확인하세요."),
}


def claude_session_error(text: str) -> str:
    """Claude CLI's JSON envelope can be cut off before its message; the session transcript still has the API
    error (for example the 5-hour usage limit). Read-only lookup in ~/.claude/projects/*/<session>.jsonl."""
    import glob
    import os
    match = re.search(r'"session_id"\s*:\s*"([0-9a-f-]{36})"', text or "")
    if not match or '"duration_api_ms":0' not in (text or "").replace(" ", ""):
        return ""
    for path in glob.glob(os.path.expanduser(f"~/.claude/projects/*/{match.group(1)}.jsonl"))[:1]:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()[-30:]
        except OSError:
            return ""
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and (event.get("isApiErrorMessage") or event.get("apiError")):
                content = ((event.get("message") or {}).get("content") or [])
                words = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                return (words + " " + str(event.get("apiError") or "")).strip()
    return ""


def classify_step(to_state: str, details: str = "") -> Dict[str, Any]:
    """Reason for a run step that ended FAIL / HUMAN_REQUIRED / TIMEOUT. ``cause`` is the AI-call reason found
    inside the message (classify()), when there is one."""
    details = details or ""
    cause = None
    if to_state == "TIMEOUT":
        code = "STEP_TIMEOUT"
    elif details.startswith("Builder failed") or "BUILDER_EXCEPTION" in details:
        code = "BUILDER_FAILED"
        extra = claude_session_error(details)
        cause = classify(error=(extra + "\n" + details.split("BUILDER_EXCEPTION:", 1)[-1]).strip())
    elif "PROVIDER_FAILURE" in details or "No usable provider" in details:
        code = "NO_PROVIDER"
        cause = classify(error=details)
    elif "USER_DECISION" in details or to_state == "HUMAN_REQUIRED":
        code = "USER_DECISION"
    elif details.startswith("Final decision"):
        code = "REVIEW_REJECTED"
    elif re.search(r"verification|build failed|tests? failed|xcodebuild", details, re.I):
        code = "VERIFY_FAILED"
    elif "EXCEPTION" in details:
        code = "DIRECTOR_ERROR"
    else:
        code = "STEP_OTHER"
    label, hint = STEP_REASONS[code]
    if cause and cause["code"] not in ("EXIT", "UNKNOWN"):
        label, hint = f"{label} · {cause['label']}", cause["hint"]
    return {"code": code, "label": label, "hint": hint, "detail": details[:600],
            "cause": cause["code"] if cause else None}
