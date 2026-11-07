"""Fork 2026-11-07: why an AI call / run step failed is kept, shown and alerted."""
import json
from types import SimpleNamespace

import pytest

from core import alerts, usage
from core.failure_reason import classify, classify_step


def result(stdout="", stderr="", exit_code=0, duration=2.5, timed_out=False, timeout_reason=None):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=exit_code, duration_sec=duration,
                           timed_out=timed_out, timeout_reason=timeout_reason)


@pytest.mark.parametrize("kwargs, code", [
    ({"error": "You've hit your 5-hour limit · resets 3:40pm (Asia/Seoul)"}, "USAGE_LIMIT"),
    ({"exit_code": 1, "stdout": json.dumps({"is_error": True, "result": "Prompt is too long"})}, "CONTEXT_TOO_LONG"),
    ({"exit_code": 1, "stderr": "Error: Not logged in. Please run /login"}, "AUTH"),
    ({"exit_code": 1, "stderr": "429 Too Many Requests"}, "RATE_LIMIT"),
    ({"exit_code": 1, "stderr": "API Error: 529 overloaded_error"}, "OVERLOADED"),
    ({"exit_code": 1, "stderr": "getaddrinfo ENOTFOUND api.example.com"}, "NETWORK"),
    ({"exit_code": -9, "timed_out": True, "timeout_reason": "NO_OUTPUT_TIMEOUT: No output for 180s"}, "NO_OUTPUT_TIMEOUT"),
    ({"exit_code": -9, "timed_out": True, "timeout_reason": "HARD_TIMEOUT: Exceeded 600s"}, "HARD_TIMEOUT"),
    ({"exit_code": -9, "error": 'GOOGLE CLI exit -9: {"event":"init","tools":["browser_get_network_request"]}'}, "INTERRUPTED"),
    ({"exit_code": 2, "stderr": "something odd"}, "EXIT"),
])
def test_classify(kwargs, code):
    assert classify(**kwargs)["code"] == code


def test_usage_limit_label_has_reset_time():
    why = classify(error="You've hit your 5-hour limit · resets 3:40pm (Asia/Seoul)")
    assert why["label"] == "사용 한도 초과 · 3:40pm (Asia/Seoul)에 풀림"


def test_model_output_mentioning_429_is_not_a_rate_limit():
    assert classify(exit_code=1, stdout="if status == 429: retry()")["code"] == "EXIT"


def test_versioned_claude_binary_is_recognised():
    assert usage.provider_of("/Users/u/.local/share/claude/versions/2.1.319") == "ANTHROPIC"
    assert usage.provider_of("/usr/local/bin/claude") == "ANTHROPIC"
    assert usage.provider_of("/opt/tool/versions/1.0") is None


def test_ledger_keeps_reason_and_lists_failures(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "notify", lambda title, message, key: sent.append((title, message, key)) or True)
    usage.set_context(role="BUILDER", stage="BUILDING", run_id="RUN-F", mission_id=None)
    rid = usage.call_started(["agy"], "p")
    usage.call_finished(rid, ["agy"], "p", result(exit_code=-9, timed_out=True,
                                                  timeout_reason="NO_OUTPUT_TIMEOUT: No output for 180s"))
    rid = usage.call_started(["claude"], "p")
    usage.call_finished(rid, ["claude"], "p", result(json.dumps({"is_error": True, "result": "You've hit your 5-hour limit"}),
                                                     exit_code=1))
    call = usage.recent_calls(1)[0]
    assert call["reason_code"] == "USAGE_LIMIT" and call["reason"].startswith("사용 한도 초과")
    f = usage.recent_failures()
    assert [c["reason_code"] for c in f["items"][:2]] == ["USAGE_LIMIT", "NO_OUTPUT_TIMEOUT"]
    assert {r["code"] for r in f["by_reason"]} >= {"USAGE_LIMIT", "NO_OUTPUT_TIMEOUT"}
    assert [k for _, _, k in sent] == ["call:Antigravity:NO_OUTPUT_TIMEOUT", "call:Claude:USAGE_LIMIT"]


def test_backfill_from_run_attempts(tmp_path):
    usage.set_context(role="BUILDER", stage="BUILDING", run_id="RUN-B", mission_id=None)
    rid = usage.call_started(["agy"], "p")
    with usage._conn() as conn:
        conn.execute("UPDATE ai_usage SET status='TIMEOUT', exit_code=-9, ended_at=started_at WHERE id=?", (rid,))
    (tmp_path / "RUN-B").mkdir()
    (tmp_path / "RUN-B" / "builder_cli_attempts.json").write_text(json.dumps([
        {"provider": "GOOGLE", "status": "FAILED", "error": "TIMEOUT: GOOGLE: NO_OUTPUT_TIMEOUT: No output for 180s"}]))
    assert usage.backfill_failure_reasons(tmp_path) == 1
    assert usage.backfill_failure_reasons(tmp_path) == 0
    assert usage.recent_calls(1)[0]["reason_code"] == "NO_OUTPUT_TIMEOUT"


@pytest.mark.parametrize("state, details, code", [
    ("FAIL", "Builder failed after 3 attempt(s) across GOOGLE: BUILDER_EXCEPTION: TIMEOUT: NO_OUTPUT_TIMEOUT", "BUILDER_FAILED"),
    ("FAIL", "Final decision: reviewer verdict 'changes_required'", "REVIEW_REJECTED"),
    ("HUMAN_REQUIRED", "Final decision: USER_DECISION: 같은 원인", "USER_DECISION"),
    ("FAIL", "MISSION_STEP_EXCEPTION: [Errno 2] No such file", "DIRECTOR_ERROR"),
    ("TIMEOUT", "", "STEP_TIMEOUT"),
])
def test_classify_step(state, details, code):
    assert classify_step(state, details)["code"] == code


def test_builder_step_label_names_the_inner_cause():
    why = classify_step("FAIL", "Builder failed after 2 attempt(s): BUILDER_EXCEPTION: TIMEOUT: GOOGLE: NO_OUTPUT_TIMEOUT: No output for 180s")
    assert why["cause"] == "NO_OUTPUT_TIMEOUT" and "출력 없이 시간 초과" in why["label"]


def test_alerts_are_off_under_tests_and_throttled(tmp_path, monkeypatch):
    assert not alerts.enabled()
    monkeypatch.setattr(alerts, "STATE", tmp_path / "a.json")
    monkeypatch.setattr(alerts, "enabled", lambda: True)
    calls = []
    monkeypatch.setattr(alerts.subprocess, "Popen", lambda *a, **k: calls.append(a))
    assert alerts.notify("t", "m", "k") and not alerts.notify("t", "m", "k") and alerts.notify("t", "m", "k2")
    assert len(calls) == 2


def test_activity_payload_carries_failures():
    import dashboard_server
    usage.set_context(role="REVIEWER", stage="REVIEWING", run_id=None, mission_id=None)
    rid = usage.call_started(["codex"], "p")
    usage.call_finished(rid, ["codex"], "p", result(stderr="Not logged in", exit_code=1))
    p = dashboard_server.build_activity_payload()
    assert p["failures"]["items"][0]["reason_code"] == "AUTH" and "steps" in p["failures"]
