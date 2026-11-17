"""
포크: Builder 파일 삭제와 같은 지적 반복 시 고치기 중단 (2026-11-17)

ClipGuard M1 미션(MSN-261117104626)에서 리뷰어가 "루트 Package.swift를 지워라"를 P1으로 냈지만 Builder 응답 형식에
삭제가 없어 같은 ORDER가 세 단계 내내 되풀이됐다(토큰만 소모, 결국 BLOCKED).

- Builder 응답에 "deletes": ["경로"]를 받는다. ORDER.allowed_deletions에 그 경로가 그대로 적혀 있을 때만 지우고,
  그 밖(목록 밖·보호 경로·없는 파일·같은 응답에서 쓰는 파일)은 응답 전체를 거부한다(아무것도 쓰지 않음).
- 고치기 라운드가 끝났는데 차단 지적(P0/P1)이 모두 직전 라운드와 같으면(문장만 바꾼 것 포함) 다음 라운드를 돌리지 않고 멈춘다.
"""
import json
import sys

import pytest

from agents.proposal_builder import ProposalBuilder
from core.orchestrator import Orchestrator
from core.research_runner import TriadResearchRunner
from core.safety import SafetyPolicy
from core.state_machine import State
from schemas.decision import DecisionSchema
from schemas.order import OrderSchema
from schemas.review_result import ReviewResultSchema
from tests.test_fork_repair_speed import GOOD, builder_sequence, orch_with_build
from tests.test_v306_head_proxy_autonomy import ORDER, quiet


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def reply(monkeypatch, proposal, seen=None):
    def invoke(self, domain, prompt, cwd, stage):
        if seen is not None:
            seen["prompt"] = prompt
        return dict(output=json.dumps(proposal), invocation="t", exit_code=0, duration_sec=0)
    monkeypatch.setattr(TriadResearchRunner, "invoke", invoke)


def order(**kw):
    base = dict(goal="g", allowed_paths=["src/**", "CHANGELOG.md"], protected_paths=["src/keep/**"])
    base.update(kw)
    return OrderSchema(**base)


# ------------------------------------------------------------------ Builder

def test_permitted_deletion_removes_the_file_and_is_reported(tmp_path, monkeypatch):
    write(tmp_path, "Package.swift", "// swift-tools-version: 6.0\n")
    write(tmp_path, "CHANGELOG.md", "- old\n")
    seen = {}
    reply(monkeypatch, {"deletes": ["Package.swift"],
                        "edits": [{"path": "CHANGELOG.md", "old": "- old", "new": "- old."}]}, seen)
    builder = ProposalBuilder(executable=sys.executable)
    res = builder.run(order(allowed_deletions=["Package.swift"]), str(tmp_path))
    assert not (tmp_path / "Package.swift").exists()
    assert sorted(res.files_modified) == ["CHANGELOG.md", "Package.swift"]
    assert builder.last_execution_info["deleted_files"] == ["Package.swift"]
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == "- old.\n"
    assert '"deletes"' in seen["prompt"] and "allowed_deletions" in seen["prompt"]
    assert "No deletion operations" not in seen["prompt"]


def test_deletion_alone_is_a_valid_reply_and_dot_slash_is_normalized(tmp_path, monkeypatch):
    write(tmp_path, "Package.swift", "x\n")
    reply(monkeypatch, {"deletes": ["./Package.swift"]})
    res = ProposalBuilder(executable=sys.executable).run(order(allowed_deletions=["Package.swift"]), str(tmp_path))
    assert res.files_modified == ["Package.swift"] and not (tmp_path / "Package.swift").exists()


@pytest.mark.parametrize("deletes, allowed, why", [
    (["src/a.py"], ["Package.swift"], "not listed"),                       # not in allowed_deletions
    (["src/a.py"], ["def:a"], "not listed"),                               # a symbol entry never authorizes a file
    (["src/keep/k.py"], ["src/keep/k.py"], "protected"),                   # protected path
    (["src/missing.py"], ["src/missing.py"], "not an existing regular file"),
    (["src/a.py", "src/a.py"], ["src/a.py"], "listed twice"),
    ([""], ["src/a.py"], "Invalid delete entry"),
])
def test_denied_deletions_reject_the_whole_reply(tmp_path, monkeypatch, deletes, allowed, why):
    write(tmp_path, "src/a.py", "A = 1\n")
    write(tmp_path, "src/keep/k.py", "K = 1\n")
    write(tmp_path, "src/b.py", "B = 0\n")
    reply(monkeypatch, {"deletes": deletes, "edits": [{"path": "src/b.py", "old": "B = 0", "new": "B = 1"}]})
    with pytest.raises(ValueError, match=why):
        ProposalBuilder(executable=sys.executable).run(order(allowed_deletions=allowed), str(tmp_path))
    assert (tmp_path / "src/a.py").exists() and (tmp_path / "src/keep/k.py").exists()
    assert (tmp_path / "src/b.py").read_text(encoding="utf-8") == "B = 0\n"          # nothing was written


def test_a_file_cannot_be_written_and_deleted_in_one_reply(tmp_path, monkeypatch):
    write(tmp_path, "src/a.py", "A = 1\n")
    reply(monkeypatch, {"deletes": ["src/a.py"], "files": [{"path": "src/a.py", "content": "A = 2\n"}]})
    with pytest.raises(ValueError, match="also written"):
        ProposalBuilder(executable=sys.executable).run(order(allowed_deletions=["src/a.py"]), str(tmp_path))
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_deletions_count_toward_the_file_budget(tmp_path, monkeypatch):
    write(tmp_path, "src/a.py", "A\n")
    write(tmp_path, "src/b.py", "B\n")
    reply(monkeypatch, {"deletes": ["src/a.py"], "files": [{"path": "src/c.py", "content": "C\n"}]})
    with pytest.raises(ValueError, match="bounded nonempty"):
        ProposalBuilder(executable=sys.executable).run(order(allowed_deletions=["src/a.py"], max_files_changed=1),
                                                       str(tmp_path))
    assert (tmp_path / "src/a.py").exists() and not (tmp_path / "src/c.py").exists()


def test_post_build_scope_guard_accepts_only_listed_deletions(tmp_path):
    ok, violations = SafetyPolicy.validate_scope_post_build(
        order=order(allowed_deletions=["Package.swift"]), numstat=[], untracked_files=[], raw_git_diff="",
        deleted_files=["Package.swift"], worktree_path=str(tmp_path))
    assert ok, violations
    ok, violations = SafetyPolicy.validate_scope_post_build(
        order=order(), numstat=[], untracked_files=[], raw_git_diff="",
        deleted_files=["Package.swift"], worktree_path=str(tmp_path))
    assert not ok and any("Unauthorized file deletion" in v for v in violations)


# ------------------------------------------------------------------ repeated findings

def history(*p1):
    return [{"round": 1, "review": {"p0": [], "p1": list(p1)}}]


def review(*p1, p0=()):
    return ReviewResultSchema(verdict="changes_required", p0=list(p0), p1=list(p1), summary="s")


def test_same_or_reworded_blocking_findings_count_as_repeated():
    prev = history("요청된 `Package.swift` 삭제가 diff에 없습니다. 루트 파일을 삭제해야 합니다.")
    assert Orchestrator.repeated_blocking_findings(prev, review(
        "요청된 Package.swift 삭제가 diff에 없습니다. 루트 파일을 삭제해야 합니다")) != []
    assert Orchestrator.repeated_blocking_findings(prev, review(
        "요청된 Package.swift 삭제가 diff에 포함되어 있지 않습니다. 루트 파일을 삭제해야 합니다.")) != []


def test_new_or_absent_findings_do_not_stop():
    prev = history("Package.swift deletion is missing from the diff")
    assert Orchestrator.repeated_blocking_findings([], review("anything")) == []              # no round yet
    assert Orchestrator.repeated_blocking_findings(prev, review()) == []                       # nothing blocking
    assert Orchestrator.repeated_blocking_findings(prev, review(
        "Package.swift deletion is missing from the diff",
        "MatchBoxEstimatorTests asserts the wrong margin")) == []                              # one finding is new
    assert Orchestrator.repeated_blocking_findings(history(), review("x")) == []               # previous had none


def test_correction_round_with_the_same_findings_stops_before_another_round(tmp_path, monkeypatch):
    orch = orch_with_build(tmp_path, max_repairs=3)
    quiet(monkeypatch, orch)
    calls = []
    v2 = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 2  # v2\n"})
    v3 = dict(GOOD, **{"feature.py": "def double(x):\n    return x * 2  # v3\n"})
    monkeypatch.setattr(orch.builder, "run", builder_sequence(calls, [GOOD, v2, v3, GOOD]))
    texts = iter(["Package.swift deletion is missing from the diff.",
                  "The requested Package.swift deletion is missing from the diff",
                  "unused", "unused"])
    monkeypatch.setattr(orch, "run_reviewer_with_failover", lambda *a: (review(next(texts)), None))
    monkeypatch.setattr(orch, "run_decision_with_failover", lambda *a: (DecisionSchema(
        decision="FAIL", rationale="delete it", next_steps=["delete Package.swift"]), None))
    out = orch.run(str(tmp_path / "proj"), "double", order=OrderSchema(**ORDER))
    assert out["final_state"] == State.FAIL
    assert len(calls) == 2                       # first build + one correction; the repeated finding stops round 2
    actions = [a["action"] for a in orch.sm.get_audit_logs(out["run_id"])]
    assert "REPAIR_REPEATED_FINDINGS" in actions


def test_failure_reason_is_translated():
    from core.failure_reason import to_korean
    assert to_korean("REPAIR_REPEATED_FINDINGS: x").startswith("같은 지적 반복으로 고치기 중단")
