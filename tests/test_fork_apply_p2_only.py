"""
포크: P2(사소한 지적)만 남은 PASS 결과도 「원본에 적용」 가능 (2026-11-17)

HEAD 대행(test_fork_p2_gate)은 HEAD PASS + 검증 통과 + P0/P1 없음이면 리뷰어 판정이 changes_required여도 최종 PASS로 둔다.
그런데 적용 조건은 reviewer_verdict == "pass"만 받아 ClipGuard M2b-2가 "final_evidence is missing or not fully PASS"로 막혔다.
"""
from core.result_manager import ResultManager


def rm(review):
    r = ResultManager.__new__(ResultManager)
    r._load = lambda run_id, name: review if name == "review.json" else None
    return r


def test_pass_verdict_is_accepted():
    assert rm(None).reviewer_accepts("R", {"reviewer_verdict": "pass"})


def test_p2_only_with_head_pass_is_accepted():
    ev = {"reviewer_verdict": "changes_required", "head_decision": "PASS"}
    assert rm({"verdict": "changes_required", "p0": [], "p1": [], "p2": ["minor"]}).reviewer_accepts("R", ev)


def test_blocking_findings_missing_review_or_head_not_pass_are_refused():
    ev = {"reviewer_verdict": "changes_required", "head_decision": "PASS"}
    assert not rm({"p0": [], "p1": ["real defect"]}).reviewer_accepts("R", ev)
    assert not rm({"p0": ["broken"], "p1": []}).reviewer_accepts("R", ev)
    assert not rm(None).reviewer_accepts("R", ev)
    assert not rm({"p0": [], "p1": []}).reviewer_accepts("R", dict(ev, head_decision="FAIL"))
    assert not rm({"p0": [], "p1": []}).reviewer_accepts("R", {"reviewer_verdict": "changes_required"})
