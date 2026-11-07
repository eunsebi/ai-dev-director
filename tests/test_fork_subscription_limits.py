"""fork 2026-11-07: subscription windows per AI (core/subscription_limits)."""
import json

from core import subscription_limits as sl


def test_parse_claude_stream_reads_unified_windows():
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "resetsAt": 100, "rateLimitType": "five_hour",
            "unifiedWindows": {"seven_day": {"utilization": 0.42, "resetsAt": 200, "status": "allowed"},
                               "five_hour": {"utilization": 0.12, "resetsAt": 100, "status": "allowed"}}}}),
        json.dumps({"type": "result", "result": "ok"}),
    ])
    out = sl.parse_claude_stream(stream)
    assert out["status"] == "allowed"
    assert [w["key"] for w in out["windows"]] == ["five_hour", "seven_day"]
    assert out["windows"][0]["used_percent"] == 12.0 and out["windows"][0]["resets_at"] == 100
    assert out["windows"][1]["label"] == "주간"


def test_parse_claude_stream_without_event():
    assert sl.parse_claude_stream('{"type":"result"}\nnot json') is None


def test_codex_limits_reads_latest_session(tmp_path, monkeypatch):
    day = tmp_path / "2026" / "11" / "07"
    day.mkdir(parents=True)
    rl = {"primary": {"used_percent": 6.0, "window_minutes": 43200, "resets_at": 300}, "secondary": None,
          "plan_type": "free", "rate_limit_reached_type": None}
    (day / "rollout-a.jsonl").write_text("\n".join([
        json.dumps({"timestamp": "2026-11-07T11:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "rate_limits": rl}}),
        json.dumps({"timestamp": "2026-11-07T11:01:00Z", "type": "event_msg", "payload": {"type": "agent_message"}}),
    ]), encoding="utf-8")
    monkeypatch.setattr(sl, "CODEX_SESSIONS", tmp_path)
    out = sl.codex_limits()
    assert out["plan"] == "free" and out["status"] == "allowed"
    assert out["windows"] == [{"key": "primary", "label": "월", "used_percent": 6.0, "resets_at": 300, "window_minutes": 43200}]


def test_codex_limits_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "CODEX_SESSIONS", tmp_path / "none")
    assert "error" in sl.codex_limits()


def test_claude_limits_uses_cache_and_respects_disable(tmp_path, monkeypatch):
    cache = tmp_path / "limits.json"
    cache.write_text(json.dumps({"claude": {"checked_at": 10**12, "windows": [{"key": "five_hour"}]}}), encoding="utf-8")
    monkeypatch.setattr(sl, "CACHE", cache)
    started = []
    monkeypatch.setattr(sl.threading, "Thread", lambda **kw: started.append(kw) or type("T", (), {"start": lambda self: None})())
    assert sl.claude_limits(10)["windows"] == [{"key": "five_hour"}]      # fresh cache: no probe
    assert started == []
    assert sl.claude_limits(0).get("disabled") is True                     # 0 = probe off
    cache.write_text(json.dumps({"claude": {"checked_at": 0}}), encoding="utf-8")
    sl.claude_limits(10)                                                   # stale: one background probe
    assert len(started) == 1
    sl._probing = False


def test_parse_agy_usage_groups():
    out = json.dumps({"status": "SUCCESS", "num_turns": 0, "command": {"name": "usage", "data": {"groups": [
        {"name": "Gemini Models", "buckets": [{"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.49,
                                               "reset_time": "2026-11-14T02:18:32Z"}]},
        {"name": "Claude and GPT models", "buckets": [{"id": "3p-weekly", "window": "weekly", "remaining_fraction": 0,
                                                      "reset_time": "2026-11-14T11:56:43Z"}]}]}}})
    parsed = sl.parse_agy_usage(out)
    w = parsed["windows"]
    assert [x["label"] for x in w] == ["Gemini 주간", "Claude·GPT 주간"]
    assert w[0]["used_percent"] == 51.0 and w[0]["status"] == "allowed"
    assert w[1]["used_percent"] == 100.0 and w[1]["status"] == "rejected"
    from datetime import datetime, timezone
    assert w[0]["resets_at"] == datetime(2026, 11, 14, 2, 18, 32, tzinfo=timezone.utc).timestamp()


def test_parse_agy_usage_bad():
    assert sl.parse_agy_usage("not json") is None
    assert sl.parse_agy_usage('{"command":{"data":{"groups":[]}}}') is None


# ---------------------------------------------------------------- manual apply -> result state (fork 2026-11-07)
def test_manual_apply_recorded_in_mission_clears_pending_results(tmp_path):
    import shutil
    from core.mission import MissionStore
    from core.result_manager import ResultManager, RESULT_APPLIED
    from core.state_machine import StateMachine, State
    sm = StateMachine()
    ids = ["RUN-TESTMANUAL-1", "RUN-TESTMANUAL-2", "RUN-TESTMANUAL-3", "RUN-TESTMANUAL-OTHER"]
    try:
        for rid in ids:
            wt = tmp_path / rid
            wt.mkdir()
            sm.create_run(rid, str(tmp_path), "g")
            sm.update_run_worktree(rid, str(wt), "b")
            for nxt in (State.PLANNING, State.ORDER_READY, State.BUILDING, State.VERIFYING, State.REVIEWING, State.DECIDING, State.PASS):
                sm.transition_to(rid, nxt, details="t")
            sm.upsert_result_state(rid, state="READY", worktree_state="PRESENT")
        shutil.rmtree(tmp_path / "RUN-TESTMANUAL-3")                         # the manual apply cleaned this worktree
        store = MissionStore()
        store.save({"mission_id": "MSN-TESTMANUAL", "status": "DONE", "steps": [{"run_id": r} for r in ids[:3]] + [{"run_id": "RUN-LATER"}],
                    "applied": {"run_id": "RUN-TESTMANUAL-2", "status": "applied", "by": "tester", "at": "2026-11-07"}})
        rm = ResultManager(sm=sm)
        assert set(rm.sync_manual_applies()) == {"RUN-TESTMANUAL-1", "RUN-TESTMANUAL-2"}   # up to the applied run only
        assert sm.get_result_state("RUN-TESTMANUAL-1")["state"] == RESULT_APPLIED
        assert sm.get_result_state("RUN-TESTMANUAL-3")["state"] == "READY"
        assert {r["run_id"] for r in rm.pending_results()} >= {"RUN-TESTMANUAL-3", "RUN-TESTMANUAL-OTHER"}
        assert rm.sync_manual_applies() == []                                # idempotent
    finally:
        for rid in ids:
            shutil.rmtree(ResultManager.run_dir(rid), ignore_errors=True)
