"""포크 2026-11-20: 남은 구독 한도가 문턱을 넘으면 단계·계획을 시작하기 전에 미리 멈추고(USAGE_LIMIT_GUARD),
풀리는 시각이 지나 한도가 실제로 풀리면 자동으로 이어서 진행한다. 실제 한도에 걸려 멈춘 미션(USAGE_LIMIT)도 같다."""
import time

from core import limit_guard
from core.mission import MissionRunner, MissionStore
from schemas.order import OrderSchema
from tests.test_fork_discord_mission_alerts import sent  # noqa: F401  (fixture)
from tests.test_v306_head_proxy_autonomy import ORDER, STEP2, mission_factory

REAL_CHECK = limit_guard.check
ON = {"limit_guard": {"enabled": True, "five_hour_percent": 90, "weekly_percent": 95}}


def snap(claude_used, resets_in=3600, codex_used=None, now=None):
    now = now or time.time()
    out = {"providers": {"ANTHROPIC": {"windows": [{"key": "five_hour", "label": "5시간", "used_percent": claude_used,
                                                    "resets_at": now + resets_in}]}, "OPENAI": {}, "GOOGLE": {}}}
    if codex_used is not None:
        out["providers"]["OPENAI"] = {"windows": [{"key": "primary", "label": "주간", "used_percent": codex_used,
                                                   "resets_at": now + 2 * resets_in, "window_minutes": 10080}]}
    return out


def test_over_limit_uses_thresholds_and_ignores_windows_already_reset():
    opts = limit_guard.settings(ON)
    now = time.time()
    assert limit_guard.over_limit(snap(80), ["ANTHROPIC"], opts, now) is None
    hit = limit_guard.over_limit(snap(91), ["ANTHROPIC"], opts, now)
    assert hit["provider"] == "ANTHROPIC" and hit["label"] == "5시간"
    assert limit_guard.over_limit(snap(99, resets_in=-10), ["ANTHROPIC"], opts, now) is None   # 이미 새 창
    # 주간 창은 weekly_percent, 여럿이면 가장 늦게 풀리는 것
    both = limit_guard.over_limit(snap(92, codex_used=96), ["ANTHROPIC", "OPENAI"], opts, now)
    assert both["provider"] == "OPENAI"
    assert limit_guard.over_limit(snap(10, codex_used=94), ["ANTHROPIC", "OPENAI"], opts, now) is None
    # 쓰지 않는 구독은 보지 않는다
    assert limit_guard.over_limit(snap(10, codex_used=99), ["ANTHROPIC"], opts, now) is None


def test_disabled_by_default_and_unreadable_limits_never_block():
    assert limit_guard.check({}) is None
    assert limit_guard.check(ON, snapshot_fn=lambda *a: (_ for _ in ()).throw(OSError("x"))) is None


def test_providers_follow_the_roles():
    cfg = {"head": {"provider": "claude"}, "builder": {"provider": "codex"}, "reviewer": {"provider": "openai"}}
    assert limit_guard.providers_in_use(cfg) == ["ANTHROPIC", "OPENAI"]


def _runner(tmp_path, monkeypatch, plans, cfg=ON):
    factory = mission_factory(monkeypatch, plans, [])

    def with_cfg():
        orch = factory()
        orch.config.update(cfg)
        return orch
    return MissionRunner(with_cfg, MissionStore(tmp_path / "missions"))


def test_guard_pauses_before_the_next_plan_and_resumes_after_reset(tmp_path, monkeypatch, sent):
    plans = [{"status": "CONTINUE", "summary": "double done", "reason": "next", "order": STEP2},
             {"status": "DONE", "summary": "all done", "reason": "goal met", "order": None}]
    runner = _runner(tmp_path, monkeypatch, plans)
    state = {"used": 50, "calls": 0}

    def fake_check(config, snapshot_fn=None):
        state["calls"] += 1
        return REAL_CHECK(config, snapshot_fn=lambda *a: snap(state["used"]))
    monkeypatch.setattr("core.limit_guard.check", fake_check)
    m = runner.create(str(tmp_path / "proj"), "double then quad", OrderSchema(**ORDER),
                      config={"autonomy": {"max_steps": 4, "auto_apply": "off"}})
    # 1단계는 통과, 계획 직전에 한도가 문턱을 넘었다
    orig = runner._execute

    def execute_then_spend(orch, mission, step):
        orig(orch, mission, step)
        if step["index"] == 1:                                       # 1단계 뒤에 한도를 많이 썼다
            state["used"] = 93
    monkeypatch.setattr(runner, "_execute", execute_then_spend)
    paused = runner.run(m["mission_id"])
    assert paused["status"] == "BLOCKED" and paused["blocked_reason"].startswith("USAGE_LIMIT_GUARD")
    assert "다음 단계 계획 전에 미리 멈춤" in paused["blocked_reason"]
    assert paused["limit_wait"]["kind"] == "guard" and paused["limit_wait"]["resume_after"] > time.time()
    assert len(paused["steps"]) == 1 and paused["steps"][0]["result"]["final_state"] == "PASS"
    title, body = sent[-1]
    assert title.startswith("⏸️ 미션 일시정지 · 한도 초과 예상") and "자동으로 이어서 진행" in body

    store = runner.store
    resumed_ids = []

    def resume(mid):
        runner.reopen(mid)
        resumed_ids.append(mid)
        return {"status": "resumed"}
    alerts = []
    # 풀리는 시각 전: 아무것도 안 함
    assert limit_guard.resume_due(ON, store, resume, lambda *a, **k: alerts.append(a)) == []
    # 시각은 지났지만 아직 문턱 위: 다음 시각만 미룸(알림 없음)
    later = paused["limit_wait"]["resume_after"] + 1
    assert limit_guard.resume_due(ON, store, resume, lambda *a, **k: alerts.append(a),
                                  snapshot_fn=lambda *a: snap(95, now=later), now=later) == []
    assert store.load(m["mission_id"])["limit_wait"]["checks"] == 1 and not alerts
    # 한도가 풀림: 재개 + 알림
    later2 = store.load(m["mission_id"])["limit_wait"]["resume_after"] + 1
    state["used"] = 20
    assert limit_guard.resume_due(ON, store, resume, lambda *a, **k: alerts.append(a),
                                  snapshot_fn=lambda *a: snap(20, now=later2), now=later2) == [m["mission_id"]]
    assert alerts and alerts[0][0] == "limit" and "이어서 진행" in alerts[0][2]
    reopened = store.load(m["mission_id"])
    assert reopened["status"] == "RUNNING" and reopened["limit_wait"] is None and reopened["limit_history"]
    done = runner.run(m["mission_id"])
    assert done["status"] == "DONE" and len(done["steps"]) == 2


def test_guard_pauses_before_the_first_step_without_running_it(tmp_path, monkeypatch, sent):
    runner = _runner(tmp_path, monkeypatch, [])
    monkeypatch.setattr("core.limit_guard.check",
                        lambda config, snapshot_fn=None: REAL_CHECK(config, snapshot_fn=lambda *a: snap(97)))
    m = runner.create(str(tmp_path / "proj"), "double", OrderSchema(**ORDER), config={"autonomy": {"auto_apply": "off"}})
    paused = runner.run(m["mission_id"])
    assert paused["status"] == "BLOCKED" and "단계 1 실행 전에" in paused["blocked_reason"]
    assert paused["steps"][0]["run_id"] is None                      # 단계를 시작하지 않았다


def test_a_real_limit_hit_gets_a_wait_record_and_auto_resumes(tmp_path, monkeypatch, sent):
    plans = [{"status": "DONE", "summary": "ok", "reason": "", "order": None}]
    runner = _runner(tmp_path, monkeypatch, plans)
    monkeypatch.setattr(MissionRunner, "_usage_limit_stop", staticmethod(lambda result: "ANTHROPIC BUILDER: 5-hour limit"))
    m = runner.create(str(tmp_path / "proj"), "double", OrderSchema(**ORDER), config={"autonomy": {"auto_apply": "off"}})
    paused = runner.run(m["mission_id"])
    assert paused["blocked_reason"].startswith("USAGE_LIMIT:") and paused["limit_wait"]["kind"] == "hit"
    assert "자동으로 이어서 진행" in sent[-1][1]
    later = (paused["limit_wait"]["resume_after"] or time.time()) + 1
    got = limit_guard.resume_due(ON, runner.store, lambda mid: {"status": "resumed"}, lambda *a, **k: None,
                                 snapshot_fn=lambda *a: snap(5, now=later), now=later)
    assert got == [m["mission_id"]]


def test_without_auto_resume_the_old_message_stays(tmp_path, monkeypatch, sent):
    plans = [{"status": "DONE", "summary": "ok", "reason": "", "order": None}]
    runner = _runner(tmp_path, monkeypatch, plans, cfg={})
    monkeypatch.setattr(MissionRunner, "_usage_limit_stop", staticmethod(lambda result: "5-hour limit"))
    m = runner.create(str(tmp_path / "proj"), "double", OrderSchema(**ORDER), config={"autonomy": {"auto_apply": "off"}})
    runner.run(m["mission_id"])
    assert "콘솔에서 「이어서 진행」" in sent[-1][1]
    assert limit_guard.resume_due({}, runner.store, lambda mid: {"status": "resumed"}, lambda *a, **k: None) == []
