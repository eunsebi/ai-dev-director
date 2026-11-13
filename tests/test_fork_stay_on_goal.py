"""2026-11-13: HEAD가 미션 목표(F6 주변 장소) 대신 검증 스크립트(scripts/logic-tests.sh)를 고치는 단계를 세우지 못하게."""
from core import autopilot
from schemas.order import OrderSchema

F6_GOAL = "어디야 F6 주변 장소 찾기 1단계(핵심 로직과 테스트). Swift Testing 단위 테스트를 만든다."


def _order(**kw):
    base = {"goal": "주변 장소 모델 보완", "allowed_paths": ["Eodiya/**", "EodiyaTests/**"], "protected_paths": [],
            "acceptance_criteria": ["테스트 통과"], "required_outputs": ["EodiyaTests/NearbyPlacesTests.swift"],
            "reference_paths": [], "allowed_deletions": [], "max_files_changed": 10, "max_diff_lines": 800,
            "context": "", "risk_level": "LOW", "requires_human_approval": False}
    base.update(kw)
    return OrderSchema(**base)


def test_infra_paths_come_from_verification_commands(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "logic-tests.sh").write_text("#!/bin/bash\n")
    cmds = (["/usr/bin/xcodebuild", "-project", "Eodiya.xcodeproj", "build"], ["/bin/bash", "scripts/logic-tests.sh"], None)
    infra = autopilot.verification_infra_paths(cmds, str(tmp_path))
    assert infra[0] == "scripts/logic-tests.sh" and ".github/**" in infra and "/usr/bin/xcodebuild" not in infra


def test_order_changing_test_runner_is_off_goal():
    infra = ["scripts/logic-tests.sh", ".github/**"]
    bad = _order(goal="테스트 러너를 프로젝트 구조에 맞게 연결", allowed_paths=["scripts/**", "Eodiya/**"],
                 required_outputs=["scripts/logic-tests.sh", "Eodiya/Features/Nearby/NearbyPlacesModel.swift"])
    assert autopilot.off_goal_infra(bad, F6_GOAL, infra) == ["scripts/logic-tests.sh"]
    named = _order(acceptance_criteria=["scripts/logic-tests.sh가 테스트 수를 정확히 센다"])
    assert autopilot.off_goal_infra(named, F6_GOAL, infra) == ["scripts/logic-tests.sh"]
    assert autopilot.off_goal_infra(_order(), F6_GOAL, infra) == []
    # 목표가 그 파일이나 검증 작업을 직접 말하면 허용
    assert autopilot.off_goal_infra(bad, "scripts/logic-tests.sh 집계 고치기", infra) == []
    assert autopilot.off_goal_infra(bad, "어디야 테스트 스크립트 정리", infra) == []


def test_accepted_order_protects_infra_files():
    infra = ["scripts/logic-tests.sh", ".github/**"]
    o = autopilot.protect_infra(_order(protected_paths=["Eodiya/Resources/**"]), F6_GOAL, infra)
    assert o.protected_paths == ["Eodiya/Resources/**", "scripts/logic-tests.sh", ".github/**"]
    assert autopilot.order_conflicts(o) == []
    assert autopilot.protect_infra(_order(), "테스트 스크립트 손보기", infra).protected_paths == []


import pytest
from agents.mock_head import MockHead
from core.mission import MissionRunner, MissionStore
from tests.test_v306_head_proxy_autonomy import ORDER, STEP2, mission_factory


@pytest.mark.head_proxy
def test_mission_planner_sends_off_goal_order_back_to_head(tmp_path, monkeypatch):
    builds, seen = [], []
    off_goal = dict(STEP2, allowed_paths=list(STEP2.get("allowed_paths") or []) + [".github/**"],
                    required_outputs=list(STEP2.get("required_outputs") or []) + [".github/workflows/test.yml"])
    plans = [{"status": "CONTINUE", "summary": "", "reason": "", "order": off_goal},
             {"status": "CONTINUE", "summary": "", "reason": "", "order": STEP2},
             {"status": "DONE", "summary": "ok", "reason": "", "order": None}]
    factory = mission_factory(monkeypatch, plans, builds)

    def plan_next(self, goal, history, ctx, cwd=None):
        seen.append(ctx.get("previous_plan_rejected"))
        return plans.pop(0)
    monkeypatch.setattr(MockHead, "plan_next", plan_next)
    runner = MissionRunner(factory, MissionStore(tmp_path / "missions"))
    m = runner.create(str(tmp_path / "proj"), "double then quad", OrderSchema(**ORDER), config={"autonomy": {"max_steps": 4}})
    done = runner.run(m["mission_id"])
    assert done["status"] == "DONE" and len(done["steps"]) == 2          # the off-goal ORDER never became a step
    assert seen[0] is None and "OFF_GOAL" in seen[1] and ".github" in seen[1]
    assert ".github/**" in done["steps"][1]["order"]["protected_paths"]  # accepted ORDER protects CI files
