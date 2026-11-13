"""2026-11-13: 어디야 F6 미션 — Claude 5시간 한도 소진을 UNKNOWN으로 보고 실패 단계로 세던 문제, Swift 테스트 수 0으로 집계."""
import json
from types import SimpleNamespace
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


def test_limit_pause_notice_says_nothing_to_decide(tmp_path):
    from core.mission import MissionStore
    sent = []
    runner = MissionRunner.__new__(MissionRunner)
    runner.store = MissionStore(tmp_path / "m")
    runner.notify = lambda text, **kw: sent.append(text)
    runner._auto_apply = lambda m: None
    m = {"mission_id": "MSN-T", "goal": "F6 2단계", "steps": [{"run_id": "RUN-1", "result": {"final_state": "PASS"}}]}
    runner._finish(m, "BLOCKED", blocked="USAGE_LIMIT: ANTHROPIC BUILDER: You've hit your 5-hour limit")
    assert "일시정지" in sent[0] and "사용자 결정 필요" not in sent[0]
    runner._finish(dict(m), "BLOCKED", blocked="USER_DECISION: 결제 필요")
    assert "사용자 결정 필요" in sent[1]


def test_resume_after_limit_pause_reruns_same_step(tmp_path, monkeypatch):
    """이어서 진행을 눌러도 예전 결과를 다시 읽고 곧바로 또 멈추던 문제(F6 2단계 MSN-261113193806)."""
    from core.mission import MissionStore
    runner = MissionRunner.__new__(MissionRunner)
    runner.store = MissionStore(tmp_path / "m")
    sent, executed = [], []
    runner.notify = lambda text, **kw: sent.append(text)
    runner.factory = lambda: SimpleNamespace(config={}, sm=SimpleNamespace(get_run=lambda rid: None))

    def fake_execute(orch, mission, step):
        executed.append(step["run_id"])
        step["run_id"] = "RUN-NEW"
    monkeypatch.setattr(MissionRunner, "_execute", lambda self, o, m, s: fake_execute(o, m, s))
    monkeypatch.setattr(MissionRunner, "_evidence", staticmethod(lambda orch, rid: {"run_id": rid, "final_state": "PASS", "files_changed": ["a"]}))
    monkeypatch.setattr(MissionRunner, "_plan", lambda self, orch, m: {"status": "DONE", "summary": "ok"})
    runner._auto_apply = lambda m: None
    m = {"mission_id": "MSN-R", "goal": "g", "project_path": str(tmp_path), "max_steps": 6, "status": "RUNNING",
         "max_consecutive_failures": 3,
         "steps": [{"index": 1, "order": {}, "run_id": "RUN-OLD", "limit_paused": True,
                    "result": {"run_id": "RUN-OLD", "final_state": "FAIL", "files_changed": []}}]}
    out = runner._advance(m)
    assert executed == [None] and m["steps"][0]["limit_retries"] == ["RUN-OLD"]
    assert m["steps"][0]["run_id"] == "RUN-NEW" and out["status"] == "DONE"
    assert any("다시 실행" in t for t in sent)
