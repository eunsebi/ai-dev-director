"""fork 2026-11-10: P2 notes never overturn a passing review (F11 MSN-261109224303 lost 3 of 6 milestones).

Before: any P1/P2 sentence containing "remove"/"delet" was a deletion warning, so a P2 note like "`intro` was removed
... so it is not a forbidden deletion" turned HEAD's PASS into FAIL. Now only the reviewer's explicit
`forbidden_deletions` (or a P0/P1) blocks; P2 notes on a passing review are kept as [P2 FOLLOW-UPS].
"""
import logging

from core.state_machine import State
from schemas.decision import DecisionSchema
from schemas.review_result import ReviewResultSchema
from tests.test_v306_head_proxy_autonomy import proxy_orch

F11_STEP1 = {  # runs/RUN-261109224303-…/review.json, shortened
    "verdict": "pass", "p0": [], "p1": [],
    "p2": ["OnboardingView.swift: the private `intro` view was removed. This is the replacement the order asks for, "
           "and `showNameStep()` keeps its signature and behaviour, so it is not a forbidden deletion.",
           "SettingsTabView: '사용 안내' is the last row of the '내 정보' section."],
    "summary": "All acceptance criteria are met."}
F11_STEP2_P2 = "the criteria's removePersistentDomain cleanup does not apply here"


def _gate(review, proxy=True):
    orch = proxy_orch() if proxy else proxy_orch(head_proxy=False)
    d = DecisionSchema(decision="PASS", rationale="HEAD: 통과", next_steps=[])
    return orch.apply_final_pass_gate(d, review, logging.getLogger("t")), d


def test_f11_step1_review_keeps_pass_and_records_follow_ups():
    state, d = _gate(ReviewResultSchema.model_validate(F11_STEP1))
    assert state == State.PASS
    assert d.rationale.startswith("HEAD: 통과") and "[P2 FOLLOW-UPS:" in d.rationale and "`intro` view was removed" in d.rationale


def test_remove_inside_a_p2_word_is_not_a_deletion():
    state, _ = _gate(ReviewResultSchema(verdict="pass", p2=[F11_STEP2_P2], summary="ok"))
    assert state == State.PASS


def test_old_review_json_without_the_flag_still_parses():
    assert ReviewResultSchema.model_validate({"verdict": "pass", "summary": "s"}).forbidden_deletions == []


def test_forbidden_deletion_and_p1_still_block():
    state, d = _gate(ReviewResultSchema(verdict="pass", forbidden_deletions=["test_login removed"], summary="s"))
    assert state == State.FAIL and "test_login removed" in d.rationale
    state, _ = _gate(ReviewResultSchema(verdict="pass", p1=["버튼이 동작하지 않음"], summary="s"))
    assert state == State.FAIL


def test_reviewer_prompts_ask_for_the_flag():
    import inspect
    from agents import claude_reviewer, codex_reviewer, google_roles
    for mod in (claude_reviewer, codex_reviewer, google_roles):
        assert "forbidden_deletions" in inspect.getsource(mod)
