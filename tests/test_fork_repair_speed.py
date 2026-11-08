"""
포크: 고치기 반복 속도 (2026-11-08)

- 빌드가 실패하면 컴파일러 오류 줄만 뽑아(build_errors) Builder·Reviewer·HEAD에 넘긴다.
- 컴파일이 안 되면 Reviewer/HEAD를 건너뛰고 바로 Builder에게 오류를 고치게 한다(compile fix, max_repairs와 별개).
- 고치기 라운드에서 Builder가 아무 파일도 바꾸지 않으면 같은 코드로 검토를 되풀이하지 않고 바로 멈춘다.
- 시간 초과(TIMEOUT)한 제공자는 같은 제공자로 다시 시도하지 않고 바로 다른 제공자로 넘긴다.
"""
import sys

import pytest

from core.state_machine import State
from core.verifier import Verifier
from schemas.builder_result import BuilderResultSchema
from schemas.decision import DecisionSchema
from schemas.order import OrderSchema
from schemas.review_result import ReviewResultSchema
from tests.test_v0234_resume_parity import _mock_orch
from tests.test_v306_head_proxy_autonomy import ORDER, PYTEST_CMD, quiet, write

XCODE_LOG = """CompileSwift normal arm64 /wt/App/A.swift (in target 'App')
    cd /wt
    /Applications/Xcode.app/.../swift-frontend -frontend -c /wt/App/A.swift -o /x/A.o
/wt/App/A.swift:266:36: error: call to main actor-isolated instance method 'go()' in a synchronous nonisolated context
/wt/App/A.swift:266:36: error: call to main actor-isolated instance method 'go()' in a synchronous nonisolated context
/wt/App/B.swift:3:1: warning: unused variable
error: Compilation failed for target 'App'
** TEST BUILD FAILED **
"""


def test_error_digest_keeps_only_compiler_errors_relative_and_deduplicated():
    errs = Verifier.error_digest(XCODE_LOG, root="/wt")
    assert errs[0] == ("App/A.swift:266:36: error: call to main actor-isolated instance method 'go()' "
                       "in a synchronous nonisolated context")
    assert len(errs) == 2 and errs[-1].startswith("error: Compilation failed")
    assert not any("warning" in e for e in errs)


@pytest.mark.parametrize("line, expected", [
    ("src/a.ts(4,7): error TS2322: Type 'string' is not assignable", "src/a.ts(4,7): error: TS2322: Type 'string' is not assignable"),
    ("SyntaxError: invalid syntax", "error: SyntaxError: invalid syntax"),
    ("main.c:9: error: expected ';'", "main.c:9: error: expected ';'"),
])
def test_error_digest_other_toolchains(line, expected):
    assert Verifier.error_digest("noise\n" + line + "\nmore noise\n") == [expected]


def test_verify_reports_build_errors_only_when_the_build_fails(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nprint('x.swift:1:2: error: nope')\nsys.exit(1)\n")
    good = tmp_path / "good.py"
    good.write_text("print('x.swift:1:2: error: just text')\n")
    failed = Verifier.verify(str(tmp_path), build_cmd=f"{sys.executable} {bad}", git_checks=False)
    assert failed["build_errors"] == ["x.swift:1:2: error: nope"]
    passed = Verifier.verify(str(tmp_path), build_cmd=f"{sys.executable} {good}", git_checks=False)
    assert passed["build_errors"] == []


# ------------------------------------------------------------------ orchestrator

def orch_with_build(tmp_path, **autonomy):
    """Build fails (with a compiler line) while feature.py contains BROKEN."""
    script = tmp_path / "fake_build.py"
    script.write_text("import os, sys\nt = open('feature.py').read() if os.path.exists('feature.py') else ''\n"
                      "if 'BROKEN' in t:\n    print('feature.py:1:5: error: BROKEN is not a token')\n    sys.exit(1)\n")
    auto = {"enabled": True, "max_repairs": 2, "head_proxy": True, "builder_attempts": 3}
    auto.update(autonomy)
    return _mock_orch(bootstrap={"enabled": True}, autonomy=auto,
                      verification={"build_command": f"{sys.executable} {script}", "test_command": PYTEST_CMD})


GOOD = {"feature.py": "def double(x):\n    return x * 2\n",
        "tests/test_feature.py": "from feature import double\n\ndef test_double():\n    assert double(4) == 8\n"}


def builder_sequence(calls, versions):
    it = iter(versions)

    def build(order, worktree_path, builder_fn=None):
        calls.append(order.context or "")
        files = next(it)
        for rel, text in files.items():
            write(worktree_path, rel, text)
        return BuilderResultSchema(status="COMPLETED", files_modified=list(files), summary="built")
    return build


def counting_review(monkeypatch, orch, seen):
    def review(rid, o, v, *a):
        seen.append(v)
        r = ReviewResultSchema(verdict="pass" if v["passed"] else "changes_required",
                               p1=[] if v["passed"] else ["broken"], summary="review")
        orch._save_json(rid, "review.json", r)
        return r, None
    monkeypatch.setattr(orch, "run_reviewer_with_failover", review)
    monkeypatch.setattr(orch, "run_decision_with_failover", lambda rid, o, b, v, r, *a: (DecisionSchema(
        decision="PASS" if v["passed"] and r.verdict == "pass" else "FAIL", rationale="evidence",
        next_steps=["fix it"]), None))


def test_compile_error_goes_straight_back_to_the_builder_without_review(tmp_path, monkeypatch):
    orch = orch_with_build(tmp_path)
    quiet(monkeypatch, orch)
    calls, reviews = [], []
    broken = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 2  # BROKEN\n"})
    monkeypatch.setattr(orch.builder, "run", builder_sequence(calls, [broken, GOOD]))
    counting_review(monkeypatch, orch, reviews)
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.PASS
    assert len(calls) == 2 and "COMPILE FIX ORDER" in calls[1]
    assert "feature.py:1:5: error: BROKEN is not a token" in calls[1]
    assert len(reviews) == 1 and reviews[0]["passed"]           # the broken build was never sent to review
    assert orch._load_json(out["run_id"], "repair_history.json") is None   # HEAD repair budget untouched
    assert "COMPILE FIX ORDER" not in (orch._load_json(out["run_id"], "order.json")["context"] or "")


def test_compile_fix_is_bounded_then_review_sees_the_errors(tmp_path, monkeypatch):
    orch = orch_with_build(tmp_path, compile_fix_rounds=1, max_repairs=0)
    quiet(monkeypatch, orch)
    calls, reviews = [], []
    b1 = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 2  # BROKEN 1\n"})
    b2 = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 2  # BROKEN 2\n"})
    monkeypatch.setattr(orch.builder, "run", builder_sequence(calls, [b1, b2]))
    counting_review(monkeypatch, orch, reviews)
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.FAIL
    assert len(calls) == 2 and len(reviews) == 1
    assert reviews[0]["build_errors"] == ["feature.py:1:5: error: BROKEN is not a token"]


def test_compile_fix_can_be_switched_off(tmp_path, monkeypatch):
    orch = orch_with_build(tmp_path, compile_fix_rounds=0, max_repairs=0)
    quiet(monkeypatch, orch)
    calls, reviews = [], []
    broken = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 2  # BROKEN\n"})
    monkeypatch.setattr(orch.builder, "run", builder_sequence(calls, [broken]))
    counting_review(monkeypatch, orch, reviews)
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.FAIL and len(calls) == 1 and len(reviews) == 1


def test_repair_round_without_any_change_stops_at_once(tmp_path, monkeypatch):
    orch = orch_with_build(tmp_path, max_repairs=3)
    quiet(monkeypatch, orch)
    calls, reviews = [], []
    wrong = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 3\n"})     # tests fail, build is fine
    monkeypatch.setattr(orch.builder, "run", builder_sequence(calls, [wrong, wrong, wrong, wrong]))
    monkeypatch.setattr(orch, "run_reviewer_with_failover", lambda *a: (ReviewResultSchema(
        verdict="changes_required", p1=["RootTabView.swift must pass the service"], summary="wiring"), None))
    monkeypatch.setattr(orch, "run_decision_with_failover", lambda *a: (DecisionSchema(
        decision="FAIL", rationale="not wired", next_steps=["edit RootTabView.swift"]), None))
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.FAIL
    assert len(calls) == 2                       # first build + one correction that changed nothing; no 3rd/4th
    actions = [a["action"] for a in orch.sm.get_audit_logs(out["run_id"])]
    assert "REPAIR_NO_PROGRESS" in actions


def test_timed_out_builder_moves_to_another_provider_immediately(tmp_path, monkeypatch):
    orch = orch_with_build(tmp_path)
    quiet(monkeypatch, orch)
    seen, calls = [], []

    def slow(order, worktree_path, builder_fn=None):
        seen.append("primary")
        raise RuntimeError("TIMEOUT: GOOGLE: HARD_TIMEOUT: Exceeded 600s")
    monkeypatch.setattr(orch.builder, "run", slow)

    class Backup:
        provider = "openai"
        run = staticmethod(builder_sequence(calls, [GOOD]))
    swaps = []

    def swap(role, run_id, logger, tried, stage, why):
        swaps.append(role)
        orch.builder = Backup()
        return True
    monkeypatch.setattr(orch, "_swap_agent", swap)
    counting_review(monkeypatch, orch, [])
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.PASS
    assert seen == ["primary"] and swaps == ["builder"] and len(calls) == 1


def test_dirty_state_keeps_the_first_file_name_whole(tmp_path):
    """git output is stripped, so the first ' M a.py' line arrives as 'M a.py' (it used to read as '.py')."""
    from core.orchestrator import Orchestrator
    from tests.test_v306_head_proxy_autonomy import git_project
    proj = git_project(tmp_path / "p", {"feature.py": "a\n", "other.py": "b\n"})
    write(proj, "feature.py", "changed\n")
    write(proj, "new.py", "n\n")
    assert set(Orchestrator._dirty_state(proj)) == {"feature.py", "new.py"}


# ------------------------------------------------------------------ focused repair prompt (ProposalBuilder)

from agents.proposal_builder import ProposalBuilder  # noqa: E402


def _repair_ctx(head_steps=(), p1=(), build_errors=(), compile_errors=()):
    import json
    ctx = "original\nHEAD REPAIR ORDER: " + json.dumps({
        "round": 1, "head": {"decision": "FAIL", "rationale": "x", "next_steps": list(head_steps)},
        "review": {"verdict": "changes_required", "p0": [], "p1": list(p1), "p2": ["ignore me"]},
        "build_errors": list(build_errors), "test_output": "long " * 1000})
    if compile_errors:
        ctx += "\nCOMPILE FIX ORDER: " + json.dumps({"round": 1, "build_errors": list(compile_errors)})
    return ctx + "\nBUILDER RETRY NOTICE: trailing text"


def test_repair_notes_and_defects_read_both_markers():
    notes = ProposalBuilder.repair_notes(_repair_ctx(["wire RootTabView"], ["P1 bug"], ["a.swift:1:1: error: x"],
                                                     ["b.swift:2:1: error: y"]))
    assert set(notes) == {"head_repair", "compile_fix"}
    assert ProposalBuilder.defects(notes) == ["b.swift:2:1: error: y", "a.swift:1:1: error: x", "wire RootTabView", "P1 bug"]
    assert ProposalBuilder.repair_notes("plain context") == {}


def test_focus_files_by_path_name_and_type_name():
    rels = ["App/RootTabView.swift", "App/Other.swift", "Friends/FriendsTabView.swift", "Friends/Map.swift", "x/util.py"]
    focus = ProposalBuilder.focus_files(
        ["Friends/FriendsTabView.swift:12:3: error: bad", "RootTabView still creates the view without the service"],
        rels, changed=["Friends/Map.swift"])
    assert focus == ["Friends/FriendsTabView.swift", "App/RootTabView.swift", "Friends/Map.swift"]


def _project(tmp_path):
    from tests.test_v306_head_proxy_autonomy import git_project
    big = "\n".join(f"let line{i} = {i}" for i in range(400)) + "\n"
    return git_project(tmp_path / "p", {"Src/Feature.swift": big, "Src/Unrelated.swift": big,
                                        "App/RootTabView.swift": "struct RootTabView {}\n"})


def _capture(builder):
    cap = {}

    def fake(prompt, order, root, repair):
        cap.update(prompt=prompt, repair=repair)
        return "RESULT"
    builder._invoke_and_apply = fake
    builder.last_execution_info = {}
    return cap


def test_repair_round_sends_only_the_defects_and_their_files(tmp_path):
    proj = _project(tmp_path)
    order = OrderSchema(goal="feature", allowed_paths=["Src/**"],
                        context=_repair_ctx(["Wire RootTabView to the service"],
                                            build_errors=["Src/Feature.swift:200:5: error: cannot find 'x'"]))
    b = ProposalBuilder(provider="antigravity", executable=sys.executable)
    cap = _capture(b)
    assert b.run(order, proj) == "RESULT" and cap["repair"] is True
    p = cap["prompt"]
    assert "REPAIR round" in p and "Src/Feature.swift:200:5: error" in p
    assert "  200> let line199 = 199" in p                       # numbered error context
    assert '"Src/Feature.swift"' in p and "struct RootTabView" in p   # editable focus + read-only named file
    assert "Unrelated.swift" in p.split("OTHER WRITABLE FILES")[1]   # listed by name only
    assert p.count("let line5 = 5") == 1                         # Unrelated's content is not sent
    assert "ignore me" not in p and "long long" not in p          # P2 and test output tail are not defects


def test_repair_round_falls_back_to_the_full_prompt_without_a_focus_file(tmp_path):
    proj = _project(tmp_path)
    order = OrderSchema(goal="feature", allowed_paths=["Src/**"], context=_repair_ctx(["make it nicer"]))
    b = ProposalBuilder(provider="antigravity", executable=sys.executable)
    cap = _capture(b)
    b.run(order, proj)
    assert cap["repair"] is False and "Implement the ORDER" in cap["prompt"]


def test_first_build_shows_swift_files_outside_allowed_paths_as_reference(tmp_path):
    proj = _project(tmp_path)
    b = ProposalBuilder(provider="antigravity", executable=sys.executable)
    cap = _capture(b)
    b.run(OrderSchema(goal="feature", allowed_paths=["Src/**"], context="see RootTabView.swift"), proj)
    assert "struct RootTabView" in cap["prompt"] and "App/RootTabView.swift" in b.last_context_files


def test_repair_round_refuses_a_complete_rewrite_of_an_existing_file(tmp_path):
    proj = _project(tmp_path)
    order = OrderSchema(goal="feature", allowed_paths=["Src/**"])
    with pytest.raises(ValueError, match="REPAIR_REWRITE"):
        ProposalBuilder.resolve_changes([{"path": "Src/Feature.swift", "content": "rewritten"}], [], proj, order, repair=True)
    # a new file and an edit are fine; outside a repair round a rewrite is allowed as before
    ok = ProposalBuilder.resolve_changes([{"path": "Src/New.swift", "content": "n"}],
                                         [{"path": "Src/Feature.swift", "old": "let line7 = 7\n", "new": "let line7 = 8\n"}],
                                         proj, order, repair=True)
    assert set(ok) == {"Src/New.swift", "Src/Feature.swift"}
    assert ProposalBuilder.resolve_changes([{"path": "Src/Feature.swift", "content": "r"}], [], proj, order)
