---
name: second-opinion
description: Codex·Antigravity에게 읽기 전용으로 「두 번째 의견」을 묻는다. 사용자가 "코덱스한테 물어봐", "agy 의견도 들어봐", "다른 AI 의견", "세컨드 오피니언", "교차 검토" 같은 말을 하거나, 설계 선택·버그 원인·방금 고친 diff를 Director 미션 없이 가볍게 검토받고 싶을 때 쓴다. 코드를 고쳐 달라는 일에는 쓰지 않는다(그건 director-send).
---

# 두 번째 의견 (second-opinion)

대화형 Claude Code(지휘자)가 Codex·Antigravity(직원)에게 짧은 질문을 **읽기 전용**으로 보내 답만 받는다.
실행기는 Director의 TRIAD 읽기 전용 실행기를 그대로 쓴다. 일회용 작업본에서 실행하고, 쓰기를 감지하면 답을 버리며,
프로세스 트리째 시간 제한을 걸고, 일시적 실패만 1번 재시도한다. 원본 프로젝트는 건드리지 않는다.

## 언제 쓰나
- 설계·구조 선택에서 다른 시각이 필요할 때
- 원인을 모르는 버그의 가설을 교차 확인할 때
- 방금 고친 변경(커밋 전)을 다른 AI에게 검토받을 때 (`--with-diff`)
- 코드 수정·구현이 필요하면 이 스킬이 아니라 Director 미션(`director-send`)을 쓴다.
- 지휘자 자신(Claude)은 직원으로 부르지 않는다.

## 순서
1. **질문 파일을 만든다** — 스크래치 폴더에 `question.md`를 쓴다. 배경, 지금 생각하는 답, 묻고 싶은 것을 구체적으로
   적는다. 비밀값·개인정보·재무 자료는 넣지 않는다. 파일 내용 전체가 OpenAI·Google 서버로 간다.
2. **누구에게 물을지 정한다** — 기본은 `codex`. 사용자가 둘 다 원하거나 중요한 판단이면 `both`.
   한도: Codex는 무료 월 한도, Antigravity는 무료 주간 한도라서 아껴 쓴다.
3. **실행한다** (오래 걸릴 수 있으니 Bash timeout을 넉넉하게, 예: 600000ms):
   ```bash
   second-opinion ask codex --file <질문 파일>
   second-opinion ask both --project <별칭|경로> --file <질문 파일>
   second-opinion ask codex --project <별칭|경로> --with-diff --file <질문 파일>
   ```
   - `--project`: 그 저장소의 마지막 커밋을 복사한 일회용 worktree에서 묻는다. 직원이 코드를 읽을 수 있다.
     별칭은 `~/.config/director-intake/projects.json`(director-submit과 같은 목록)에 있다.
   - `--project`가 없으면 빈 폴더에서 질문만 전달한다.
   - `--with-diff`: 커밋 안 된 변경(`git diff HEAD`)을 붙인다. `.env*`, 키, 토큰, `CLAUDE.md`, `config.yaml` 등은 빠진다.
     새로 만든(추적 안 된) 파일은 diff에 없으므로 필요하면 질문에 적는다.
   - 한도 문턱(config `limit_guard` 값)을 넘은 직원은 건너뛴다. 사용자가 원할 때만 `--force`.
4. **결과를 정리한다** — 출력 JSON의 `results.<직원>.answer`를 읽고 사용자에게 이렇게 전한다.
   - 직원별 핵심 의견 한두 줄
   - **Claude 판단과 다른 점**과 그에 대한 Claude의 평가(그대로 따르지 말고 근거로 검토)
   - 최종 추천
   - 실패한 직원이 있으면 `error_ko`와 `next`를 그대로 알린다.
   - 원문 기록 경로는 `report`(Director `runs/second-opinion/…/report.md`)다.

## 상태 확인
```bash
second-opinion check
```
CLI 경로·버전·한도만 보고 모델은 부르지 않는다. 로그인 여부는 실제 질문을 해 봐야 안다.

## 설치 (맥, 한 번만)
```bash
ln -sf ~/work/ai-dev-director/tools/second-opinion.py ~/.local/bin/second-opinion
```
```bash
mkdir -p ~/.claude/skills/second-opinion && cp ~/work/ai-dev-director/tools/skills/second-opinion/SKILL.md ~/.claude/skills/second-opinion/
```
스크립트는 Director `.venv`로 알아서 다시 실행된다. 복사본이 아니라 심볼릭 링크로 걸어 두어야 Director 위치를 찾는다
(복사했다면 `DIRECTOR_HOME`을 지정).

## 하지 않는 것
- 직원에게 파일 수정·명령 실행·외부 전송을 시키지 않는다(쓰기를 감지하면 답을 버림).
- Director 안의 비대화형 실행(`claude --print`)에서는 쓰지 않는다.
- 실패를 성공처럼 보고하지 않는다. 빈 응답·시간 초과·오류는 실패다.
