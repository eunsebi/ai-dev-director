"""2026-11-13: 어디야 F6 미션 — Claude 5시간 한도 소진을 UNKNOWN으로 보고 실패 단계로 세던 문제, Swift 테스트 수 0으로 집계."""
import json
from types import SimpleNamespace

from core.mission import MissionRunner
from core.provider_manager import ErrorCategory
from core.research_runner import TriadResearchRunner as ResearchRunner
from core.verifier import Verifier

CLAUDE_LIMIT = json.dumps({"type": "result", "subtype": "success", "is_error": True, "duration_api_ms": 0,
                           "usage": {"input_tokens": 0, "output_tokens": 0, "iterations": [], "pad": "x" * 600},
                           "result": "You've hit your 5-hour limit · resets 3:30pm (Asia/Seoul)", "api_error_status": 429})


def test_claude_error_text_pulls_result_from_long_json():
    text = ResearchRunner._claude_error_text(CLAUDE_LIMIT)
    assert "hit your 5-hour limit" in text and "429" in text
    assert ResearchRunner._claude_error_text("not json") == ""
    msg = "ANTHROPIC CLI exit 1: " + text[:300] + " | " + CLAUDE_LIMIT[:500]
    assert ErrorCategory.classify(msg) == ErrorCategory.QUOTA
    assert ErrorCategory.classify("ANTHROPIC CLI exit 1: " + CLAUDE_LIMIT[:500]) != ErrorCategory.QUOTA  # 예전 잘린 메시지


def test_swift_testing_and_xctest_counts():
    ok = "✔ Test a() passed after 0.01 seconds.\n✔ Test run with 169 tests in 25 suites passed after 0.112 seconds."
    assert Verifier._parse_test_counts(ok) == {"passed": 169, "failed": 0, "total": 169}
    xc = ("Test Suite 'A' passed.\n\t Executed 3 tests, with 0 failures (0 unexpected)\n"
          "Test Suite 'All tests' passed.\n\t Executed 12 tests, with 0 failures (0 unexpected)\n"
          "✔ Test run with 5 tests in 2 suites passed after 0.1 seconds.")
    assert Verifier._parse_test_counts(xc) == {"passed": 17, "failed": 0, "total": 17}
    bad = ("✘ Test b() failed after 0.01 seconds with 1 issue.\n✘ Test c() failed after 0.02 seconds with 1 issue.\n"
           "✘ Test run with 10 tests in 3 suites failed after 0.1 seconds with 2 issues.")
    assert Verifier._parse_test_counts(bad) == {"passed": 8, "failed": 2, "total": 10}
    assert Verifier._parse_test_counts("3 passed, 1 failed in 0.2s")["passed"] == 3  # pytest 그대로


def test_usage_limit_step_stops_mission(monkeypatch):
    from core import usage
    rows = [{"provider": "ANTHROPIC", "role": "BUILDER", "error_detail": "You've hit your 5-hour limit · resets 3:30pm"}]
    monkeypatch.setattr(usage, "_rows", lambda where, params: rows if params == ("RUN-X",) else [])
    stop = MissionRunner._usage_limit_stop({"run_id": "RUN-X", "final_state": "FAIL", "files_changed": []})
    assert stop and "5-hour limit" in stop and "ANTHROPIC BUILDER" in stop
    # 대체 AI가 결과를 냈거나, 통과했거나, 한도 기록이 없으면 멈추지 않는다
    assert MissionRunner._usage_limit_stop({"run_id": "RUN-X", "final_state": "FAIL", "files_changed": ["a.swift"]}) is None
    assert MissionRunner._usage_limit_stop({"run_id": "RUN-X", "final_state": "PASS", "files_changed": []}) is None
    assert MissionRunner._usage_limit_stop({"run_id": "RUN-Y", "final_state": "FAIL", "files_changed": []}) is None
