"""fork 2026-11-09: the 실패 원인 panel in Korean.

New runs: Reviewer and HEAD write their prose in Korean, and the Director's own override rationale is Korean and
names the reviewer's actual findings. Old records: the Director's fixed English phrases are translated for display.
"""
import logging

from core import failure_reason as fr
from core.state_machine import State
from schemas.decision import DecisionSchema
from schemas.review_result import ReviewResultSchema
from tests.test_v306_head_proxy_autonomy import proxy_orch

OLD = ("Final decision: Overridden by Orchestrator: Reviewer reported existing behavior loss or P1 concerns: "
       "Read-only review of Eodiya/Features/Guide/GuideView.swift; build passed, no tests configured. All three "
       "requested fixes are in.")


def test_old_english_record_gets_korean_fixed_phrases():
    step = fr.classify_step("FAIL", OLD)
    assert step["code"] == "REVIEW_REJECTED"                      # detection still uses the English original
    ko = step["detail_ko"]
    assert ko.startswith("최종 판정: Director가 HEAD 판정을 뒤집음 · 리뷰어가 기존 동작 손실 또는 P1")
    assert "빌드 통과" in ko and "설정된 테스트 없음" in ko
    assert step["english"] is True                                # the reviewer's own sentence is still English
    assert fr.to_korean("Final decision: USER_DECISION: 결제가 필요") == "최종 판정: 사용자 결정 필요: 결제가 필요"
    assert fr.to_korean("reviewer verdict 'changes_required' is not 'pass' (x)").startswith(
        "리뷰어 판정이 통과가 아님('changes_required')")


def test_korean_text_is_not_flagged_english():
    assert fr.mostly_english("리뷰어가 GuideView.swift의 card(_:)에서 스크롤이 막히는 문제를 찾았습니다. 고쳐야 합니다.") is False
    assert fr.classify_step("FAIL", "Final decision: 리뷰어가 P1을 보고함: 버튼이 사라졌습니다")["english"] is False


def test_override_rationale_is_korean_and_names_the_findings():
    orch = proxy_orch()
    d = DecisionSchema(decision="PASS", rationale="ok", next_steps=[])
    review = ReviewResultSchema(verdict="pass", p1=["설정 화면의 '사용 안내' 줄이 눌리지 않음"], p2=["문구 다듬기"],
                                summary="요청한 세 가지는 반영됨")
    assert orch.apply_final_pass_gate(d, review, logging.getLogger("t")) == State.FAIL
    assert d.rationale.startswith("Director가 HEAD의 PASS를 뒤집음 · 리뷰어가 기존 동작 손실 또는 P1")
    assert "P1(꼭 고칠 문제): 설정 화면의 '사용 안내' 줄이 눌리지 않음" in d.rationale
    assert "리뷰어 요약: 요청한 세 가지는 반영됨" in d.rationale

    d2 = DecisionSchema(decision="PASS", rationale="ok", next_steps=[])
    p0 = ReviewResultSchema(verdict="changes_required", p0=["빌드 깨짐"], summary="s")
    assert orch.apply_final_pass_gate(d2, p0, logging.getLogger("t")) == State.FAIL
    assert d2.rationale == "Director가 HEAD의 PASS를 뒤집음 · 리뷰어가 P0(치명적 문제)를 보고함: 빌드 깨짐"


def test_korean_deletion_finding_still_blocks_pass():
    orch = proxy_orch()
    d = DecisionSchema(decision="PASS", rationale="ok", next_steps=[])
    review = ReviewResultSchema(verdict="pass", p2=["기존 OnboardingView의 로고가 삭제됨"], summary="s")
    assert orch.apply_final_pass_gate(d, review, logging.getLogger("t")) == State.FAIL
    assert "삭제 관련 지적: 기존 OnboardingView의 로고가 삭제됨" in d.rationale


def test_prompts_ask_for_korean():
    import inspect
    from agents import claude_head, codex_head, claude_reviewer, codex_reviewer, google_roles
    for mod in (claude_reviewer, codex_reviewer, google_roles):
        assert "REVIEW_LANGUAGE_RULE" in inspect.getsource(mod)
    for mod in (claude_head, codex_head):
        assert "+ DECISION_LANGUAGE_RULE +" in inspect.getsource(mod)
    assert "USER_DECISION:" in fr.DECISION_LANGUAGE_RULE and "한국어" in fr.REVIEW_LANGUAGE_RULE
