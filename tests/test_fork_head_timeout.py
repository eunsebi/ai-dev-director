"""포크 2026-11-15: 시간 초과한 HEAD는 같은 공급자로 다시 묻지 않는다.

맥 run 기록(11-08~11-15): Claude HEAD 호출 18번이 출력 없이 시간 제한(300초, 11-14부터 180초)까지 멈췄다.
HEAD가 쓴 120분 중 86분이다. 한도 직전에 자주 멈췄고, 같은 공급자 재시도가 또 멈춘 경우가 5번(한 번에 5분씩)이었다.
빌더는 11-08부터 시간 초과면 바로 다른 공급자로 넘기는데 HEAD 판정·미션 계획은 그렇지 않았다.
"""
from agents.mock_head import MockHead
from core.mission import MissionRunner, MissionStore
from core.state_machine import State
from schemas.decision import DecisionSchema
from schemas.order import OrderSchema
from tests.test_v306_head_proxy_autonomy import (ORDER, STEP2, good_build, mission_factory, proxy_orch, quiet,
                                                 review_green)

HUNG = "TIMEOUT: Claude Head CLI exceeded limit: HARD_TIMEOUT: Exceeded 180s"


class BackupHead:
    provider = "openai"

    def decide(self, *a, **k):
        return DecisionSchema(decision="PASS", rationale="backup HEAD: tests and review green")


def _decision_orch(monkeypatch, error):
    orch = proxy_orch()
    quiet(monkeypatch, orch)
    monkeypatch.setattr(orch.builder, "run", good_build([]))
    review_green(monkeypatch, orch)
    tries, swaps = [], []

    def fail(*a, **k):
        tries.append(1)
        raise TimeoutError(error) if "TIMEOUT" in error else RuntimeError(error)
    monkeypatch.setattr(orch.head, "decide", fail)
    return orch, tries, swaps


def test_timed_out_head_decision_moves_to_another_provider_at_once(tmp_path, monkeypatch):
    orch, tries, swaps = _decision_orch(monkeypatch, HUNG)

    def swap(role, run_id, logger, tried, stage, why):
        swaps.append((role, stage))
        orch.head = BackupHead()
        return True
    monkeypatch.setattr(orch, "_swap_agent", swap)
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.PASS
    assert tries == [1]                                   # the hung HEAD was asked once, not twice
    assert swaps == [("head", "DECIDING")]


def test_timed_out_head_without_another_provider_gets_its_one_retry(tmp_path, monkeypatch):
    orch, tries, swaps = _decision_orch(monkeypatch, HUNG)
    monkeypatch.setattr(orch, "_swap_agent", lambda *a: swaps.append(1) or False)
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert len(tries) == 2                                # same HEAD once more, as before
    assert out["final_state"] == State.PASS               # then the delegated evidence rule (tests + review green)
    assert "DECISION_FALLBACK" in [a["action"] for a in orch.sm.get_audit_logs(out["run_id"])]


def test_other_head_errors_still_retry_the_same_provider_first(tmp_path, monkeypatch):
    orch, tries, swaps = _decision_orch(monkeypatch, "unexpected JSON in HEAD answer")

    def swap(role, run_id, logger, tried, stage, why):
        swaps.append(len(tries))
        orch.head = BackupHead()
        return True
    monkeypatch.setattr(orch, "_swap_agent", swap)
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.PASS
    assert len(tries) == 2 and swaps == [2]               # retried once, then replaced


def test_timed_out_mission_planner_moves_to_another_provider_at_once(tmp_path, monkeypatch):
    builds, swaps, calls = [], [], []
    plans = [TimeoutError(HUNG),
             {"status": "CONTINUE", "summary": "double done", "reason": "next", "order": STEP2},
             {"status": "DONE", "summary": "ok", "reason": "goal met", "order": None}]
    base = mission_factory(monkeypatch, plans, builds)

    def plan_next(self, goal, history, ctx, cwd=None):
        item = plans.pop(0)
        calls.append(type(item).__name__)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(MockHead, "plan_next", plan_next)

    def factory():
        orch = base()
        monkeypatch.setattr(orch, "_swap_agent",
                            lambda role, run_id, logger, tried, stage, why: swaps.append((stage, len(calls))) or True)
        return orch
    runner = MissionRunner(factory, MissionStore(tmp_path / "missions"))
    m = runner.create(str(tmp_path / "proj"), "double then quad", OrderSchema(**ORDER), config={"autonomy": {"max_steps": 4}})
    done = runner.run(m["mission_id"])
    assert done["status"] == "DONE"
    assert swaps[0] == ("MISSION_PLANNING", 1)            # swapped right after the first (hung) planning call
