"""fork 2026-11-07: delivery pipeline after 「원본에 적용」 (core/apply_pipeline.py).

apply(+build) → command checks → manual checks (⌘U) → commit → cleanup → record; a failed check or a manual "실패"
rolls the project back and sends the mission back to HEAD with the failure as feedback.
"""
import subprocess
import sys

import pytest

from core.apply_pipeline import Pipeline, plan_steps, project_pipeline
from core.bootstrap import snapshot
from core.mission import MissionStore
from core.result_manager import ResultManager, RESULT_APPLIED, RESULT_VERIFY_FAILED
from core.state_machine import State
from tests.test_v312_delivery import project, fake_run, cleanup_runs  # noqa: F401


def _mission(proj, rid, mid):
    store = MissionStore()
    store.save({"mission_id": mid, "project_path": str(proj), "goal": "a를 2로 바꾸고 new.py를 추가한다", "status": "DONE",
                "max_steps": 3, "base_worktree": None, "summary": "끝",
                "steps": [{"index": 1, "run_id": rid, "order": {"goal": "g"},
                           "result": {"final_state": State.PASS, "worktree_path": None}}]})
    return store


def _pipeline(proj, pipe, store, calls):
    cfg = {"projects": {str(proj): {"apply_pipeline": pipe}}}
    hooks = {"load_config": lambda: cfg, "chat": lambda text, run_id=None: calls.setdefault("chat", []).append(text),
             "invalidate": lambda: None, "notify": lambda t, msg, key: calls.setdefault("notify", []).append((t, msg)),
             "extend": lambda mid, reason: calls.setdefault("extend", []).append((mid, reason)) or {"status": "resumed"}}
    return Pipeline(store, ResultManager(config={"verification": {"test_command": None}}), hooks)


def _ready(tmp_path, rid):
    proj = project(tmp_path)
    wt = fake_run(proj, rid, snapshot(str(proj)))
    (wt / "a.py").write_text("a = 2\n", encoding="utf-8")
    (wt / "new.py").write_text("new = 1\n", encoding="utf-8")
    return proj, wt


def git_out(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout


def test_config_and_plan():
    assert project_pipeline({"projects": {"/x": {"verification": {}}}}, "/x") is None
    pipe = {"checks": [{"name": "규칙", "cmd": ["true"]}], "manual_checks": [{"name": "⌘U"}], "record": {"memory": "m"}}
    assert project_pipeline({"projects": {"/x": {"apply_pipeline": pipe}}}, "/x") == pipe
    assert [s["kind"] for s in plan_steps(pipe)] == ["apply", "check", "manual", "commit", "cleanup", "record"]
    assert [s["kind"] for s in plan_steps({"commit": False, "cleanup": False})] == ["apply"]


def test_happy_path_waits_for_manual_check_then_commits_and_cleans_up(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PIPE1", "MSN-TEST-PIPE1"; cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"checks": [{"name": "규칙 테스트", "cmd": [sys.executable, "-c", "print('23 passing')"]}],
            "manual_checks": [{"name": "Swift 테스트 실행(⌘U)", "instructions": "Xcode에서 ⌘U"}]}
    p = _pipeline(proj, pipe, store, calls)
    assert p.start(mid, rid, background=False)["status"] == "pipeline_started"
    d = store.load(mid)["delivery"]
    assert d["status"] == "AWAITING_USER" and d["waiting"]["name"] == "Swift 테스트 실행(⌘U)"
    assert [s["status"] for s in d["steps"]] == ["ok", "ok", "waiting", "pending", "pending"]
    assert (proj / "a.py").read_text(encoding="utf-8") == "a = 2\n"           # applied, not yet committed
    assert calls["notify"] and "⌘U" in calls["notify"][-1][1]
    assert p.start(mid, rid, background=False)["status"] == "busy"            # one delivery at a time

    assert p.answer_manual(mid, True, background=False)["status"] == "continued"
    d = store.load(mid)["delivery"]
    assert d["status"] == "DONE" and d["commit"]
    assert [s["status"] for s in d["steps"]] == ["ok"] * 5
    committed = set(git_out(proj, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {"a.py", "new.py"}                                     # only the applied files
    assert "u.txt" in git_out(proj, "status", "--porcelain")                   # the user's own file is untouched
    assert not wt.exists()                                                     # worktree removed
    assert ResultManager().result_state(rid)["state"] == RESULT_APPLIED


def test_failed_check_rolls_back_and_sends_mission_to_head(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PIPE2", "MSN-TEST-PIPE2"; cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"checks": [{"name": "보안 규칙 테스트", "cmd": [sys.executable, "-c", "import sys; print('rule R-3 denied'); sys.exit(1)"]}],
            "manual_checks": [{"name": "⌘U"}]}
    _pipeline(proj, pipe, store, calls).start(mid, rid, background=False)
    m = store.load(mid)
    assert m["delivery"]["status"] == "REWORK"
    assert [s["status"] for s in m["delivery"]["steps"]][:3] == ["ok", "failed", "pending"]
    assert (proj / "a.py").read_text(encoding="utf-8") == "a = 1\n" and not (proj / "new.py").exists()   # rolled back
    assert m["applied"]["status"] == "rolled_back"
    assert "rule R-3 denied" in m["steps"][-1]["result"]["post_apply_failure"]     # HEAD sees the output
    assert calls["extend"] and calls["extend"][0][0] == mid
    assert wt.exists()                                                         # the next milestone builds on it
    assert ResultManager().result_state(rid)["state"] == RESULT_VERIFY_FAILED
    assert git_out(proj, "log", "--oneline").count("\n") == 1                  # nothing committed


def test_manual_fail_rolls_back_with_the_users_note(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PIPE3", "MSN-TEST-PIPE3"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid)
    store, calls = _mission(proj, rid, mid), {}
    p = _pipeline(proj, {"manual_checks": [{"name": "Swift 테스트 실행(⌘U)"}]}, store, calls)
    p.start(mid, rid, background=False)
    assert p.answer_manual(mid, False, note="MapModelTests.hidesAfter24h 실패", background=False)["status"] == "rework"
    m = store.load(mid)
    assert m["delivery"]["status"] == "REWORK"
    assert "hidesAfter24h" in m["steps"][-1]["result"]["post_apply_failure"]
    assert (proj / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert p.answer_manual(mid, True, background=False)["status"] == "refused"   # nothing waits any more


def test_blocked_apply_waits_for_the_user_instead_of_rework(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PIPE4", "MSN-TEST-PIPE4"; cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    (proj / "a.py").write_text("a = 99\n", encoding="utf-8")                  # the user changed the same file
    store, calls = _mission(proj, rid, mid), {}
    _pipeline(proj, {}, store, calls).start(mid, rid, background=False)
    m = store.load(mid)
    assert m["delivery"]["status"] == "BLOCKED" and "extend" not in calls
    assert (proj / "a.py").read_text(encoding="utf-8") == "a = 99\n"


def test_record_step_uses_restricted_claude_and_reports_its_answer(tmp_path, cleanup_runs, monkeypatch):
    rid, mid = "RUN-TEST-PIPE5", "MSN-TEST-PIPE5"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid)
    fake = tmp_path / "claude"
    args_file = tmp_path / "args.txt"
    fake.write_text("#!/bin/sh\n"
                    f"echo \"$@\" > {args_file}\n"
                    "cat > /dev/null\n"
                    "echo '{\"result\": \"{\\\"memory\\\":\\\"ok\\\",\\\"claude_md\\\":\\\"ok\\\",\\\"note\\\":\\\"done\\\"}\"}'\n",
                    encoding="utf-8")
    fake.chmod(0o755)
    import core.env_utils as eu
    monkeypatch.setenv("DIRECTOR_CLAUDE_JSON", str(tmp_path / "no-claude.json"))   # never copy the real MCP config
    monkeypatch.setattr(eu, "resolve_cli", lambda name: str(fake) if name == "claude" else None)
    store, calls = _mission(proj, rid, mid), {}
    _pipeline(proj, {"cleanup": False, "record": {"memory": "eodiya-project"}}, store, calls).start(mid, rid, background=False)
    d = store.load(mid)["delivery"]
    assert d["status"] == "DONE", d
    rec = [s for s in d["steps"] if s["kind"] == "record"][0]
    assert rec["status"] == "ok" and "메모리 ok" in rec["detail"]
    args = args_file.read_text(encoding="utf-8")
    assert "Edit(./CLAUDE.md)" in args and "mcp__claude-memory__memory_append" in args
    assert "Bash" in args.split("--disallowedTools")[1]                      # no shell for the recorder
