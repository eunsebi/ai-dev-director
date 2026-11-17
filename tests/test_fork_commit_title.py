"""
포크: 「원본에 적용」 커밋·개발일지 제목 (2026-11-17)

디스코드 지시문 첫 줄이 그대로 제목이 되어 "ClipGuard M2b-1 영상 분석 만들어줘. docs/director/M2b1-analysis.md 명세대로, 결정은 do…"
처럼 잘렸다. 첫 문장에서 요청 어미(만들어줘·고쳐 주세요…)를 떼고, 동사가 뜻을 가지면(고쳐→수정, 추가해→추가) 명사로 붙인다.
요청 원문은 본문 "요청:" 줄에 남긴다. 평서문 목표는 그대로 둔다.
"""
import pytest

from core.apply_pipeline import Pipeline, title_from_goal


@pytest.mark.parametrize("goal, title", [
    ("ClipGuard M2b-1 영상 분석 만들어줘. docs/director/M2b1-analysis.md 명세대로, 결정은 docs/spec/01-design.md 10절",
     "ClipGuard M2b-1 영상 분석"),
    ("어디야 F10 2단계 프로필 사진 만들어줘. docs/spec/08-profile.md 명세(9절 확정 칸 참고)", "어디야 F10 2단계 프로필 사진"),
    ("로그인 버그 고쳐 주세요", "로그인 버그 수정"),
    ("다크 모드 설정 추가해줘.", "다크 모드 설정 추가"),
    ("README를 정리해줘", "README 정리"),
    ("a를 2로 바꾸고 new.py를 추가한다", "a를 2로 바꾸고 new.py를 추가한다"),     # plain statement: unchanged
    ("해줘", "해줘"),                                                             # too short: whole line
    ("  여러   칸   띄움 만들어줘  ", "여러 칸 띄움"),
])
def test_title_from_goal(goal, title):
    assert title_from_goal(goal) == title


def test_commit_message_keeps_the_request_in_the_body():
    m = {"goal": "ClipGuard M2b-1 영상 분석 만들어줘. docs/director/M2b1-analysis.md 명세대로.\n둘째 줄",
         "summary": "분석 완성", "mission_id": "MSN-1"}
    d = {"run_id": "RUN-1", "steps": [{"kind": "manual", "name": "⌘U", "status": "ok"}]}
    msg = Pipeline.commit_message(m, d, ["X <x@example.com>"])
    lines = msg.splitlines()
    assert lines[0] == "ClipGuard M2b-1 영상 분석"
    assert lines[2] == "분석 완성"
    assert lines[3].startswith("요청: ClipGuard M2b-1 영상 분석 만들어줘. docs/director/M2b1-analysis.md 명세대로. 둘째 줄")
    assert "Director 미션 MSN-1 · 결과 run RUN-1" in msg and msg.rstrip().endswith("Co-Authored-By: X <x@example.com>")


def test_plain_goal_has_no_request_line():
    m = {"goal": "a를 2로 바꾸고 new.py를 추가한다", "summary": "끝", "mission_id": "MSN-2"}
    msg = Pipeline.commit_message(m, {"run_id": "R", "steps": []}, [])
    assert msg.startswith("a를 2로 바꾸고 new.py를 추가한다\n\n끝\nDirector 미션 MSN-2") and "요청:" not in msg
