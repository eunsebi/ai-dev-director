"""포크 2026-11-17: 미션 진행(시작·단계 결과·종료·한도 일시정지·자동 적용·정지)을 디스코드로 알린다.
콘솔을 계속 보고 있지 않으면 미션이 끝났는지, 실패했는지, 한도로 멈췄는지 알 수 없었다."""
import threading
import types

import pytest

from core import alerts
from core.mission import MissionRunner, MissionStore
from schemas.order import OrderSchema
from tests.test_v306_head_proxy_autonomy import ORDER, STEP2, mission_factory


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(alerts, "_testing", lambda: False)
    monkeypatch.setattr(alerts, "enabled", lambda: False)                    # no Mac banner in tests
    monkeypatch.setattr(alerts, "discord_webhook", lambda: "https://discord.com/api/webhooks/1/x")
    monkeypatch.setattr(alerts, "_post_discord", lambda url, title, message: out.append((title, message)))
    seen = set()
    monkeypatch.setattr(alerts, "_due", lambda key: key not in seen and not seen.add(key))

    class Inline:                                                           # run the post right away
        def __init__(self, target, args, daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)
    # only inside core.alerts: the Director's own process runner needs the real threads
    monkeypatch.setattr(alerts, "threading", types.SimpleNamespace(Thread=Inline, Lock=threading.Lock))
    return out


def _run_mission(tmp_path, monkeypatch, plans):
    runner = MissionRunner(mission_factory(monkeypatch, plans, []), MissionStore(tmp_path / "missions"))
    m = runner.create(str(tmp_path / "proj"), "double then quad", OrderSchema(**ORDER),
                      config={"autonomy": {"max_steps": 4, "auto_apply": "off"}})
    return runner.run(m["mission_id"])


def test_a_finished_mission_reports_start_each_step_and_the_end(tmp_path, monkeypatch, sent):
    plans = [{"status": "CONTINUE", "summary": "double done", "reason": "next", "order": STEP2},
             {"status": "DONE", "summary": "double and quad work", "reason": "goal met", "order": None}]
    done = _run_mission(tmp_path, monkeypatch, plans)
    assert done["status"] == "DONE"
    titles = [t for t, _ in sent]
    assert titles == ["▶️ 미션 시작 · proj", "✅ 단계 1/4 통과 · proj", "✅ 단계 2/4 통과 · proj",
                      "🎉 미션 완료 · proj"]
    end = sent[-1][1]
    assert "목표: double then quad" in end and "단계: 2/2 통과" in end and "요약: double and quad work" in end
    assert done["mission_id"] in end and "경과" in end


def test_a_usage_limit_pause_says_nothing_needs_deciding(tmp_path, monkeypatch, sent):
    plans = [{"status": "BLOCKED", "summary": "", "reason": "x", "order": None}]
    monkeypatch.setattr(MissionRunner, "_usage_limit_stop", staticmethod(lambda result: "5-hour limit"))
    done = _run_mission(tmp_path, monkeypatch, plans)
    assert done["status"] == "BLOCKED"
    title, body = sent[-1]
    assert title == "⏸️ 미션 일시정지 · AI 사용 한도 · proj"
    assert "이어서 진행" in body


def test_discord_events_choose_what_is_sent(tmp_path, monkeypatch, sent):
    monkeypatch.setattr(alerts, "discord_events", lambda: {"mission_end"})
    plans = [{"status": "DONE", "summary": "ok", "reason": "", "order": None}]
    _run_mission(tmp_path, monkeypatch, plans)
    assert [t for t, _ in sent] == ["🎉 미션 완료 · proj"]


def test_discord_events_default_and_filter(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(alerts, "CONFIG", cfg)
    cfg.write_text("alerts:\n  discord_webhook: ''\n")
    assert alerts.discord_events() == set(alerts.MISSION_EVENTS)
    cfg.write_text("alerts:\n  discord_events: [mission_end, apply, nonsense]\n")
    assert alerts.discord_events() == {"mission_end", "apply"}


def test_a_mission_steps_failure_is_not_sent_twice(tmp_path, monkeypatch, sent):
    monkeypatch.setattr(alerts, "_in_mission", lambda run_id: run_id == "RUN-IN-MISSION")
    alerts.step_failed("RUN-IN-MISSION", "FAIL", "Verification failed: Build: FAIL")
    assert sent == []                                    # the mission's own step message covers it
    alerts.step_failed("RUN-ALONE", "FAIL", "Verification failed: Build: FAIL")
    assert len(sent) == 1 and "실패" in sent[0][0]


def test_alerts_never_break_the_mission_without_a_webhook(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "_testing", lambda: False)
    monkeypatch.setattr(alerts, "enabled", lambda: False)
    monkeypatch.setattr(alerts, "discord_webhook", lambda: "")
    plans = [{"status": "DONE", "summary": "ok", "reason": "", "order": None}]
    assert _run_mission(tmp_path, monkeypatch, plans)["status"] == "DONE"


def test_incomplete_says_whether_planning_failed_or_the_budget_ran_out(tmp_path, monkeypatch, sent):
    # fork 2026-11-17: ClipGuard M2b-3 stopped because HEAD could not plan step 2, the console said "예산 소진"
    done = _run_mission(tmp_path, monkeypatch, [{"status": "GIVE_UP", "summary": "", "reason": "OFF_GOAL: x", "order": None}])
    assert done["status"] == "INCOMPLETE" and done["blocked_reason"].startswith("HEAD could not plan")
    assert sent[-1][0] == "⏹️ 미션 중단 · 다음 단계 계획 실패 · proj"
