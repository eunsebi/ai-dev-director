# Fork notes (eunsebi/ai-dev-director)

Upstream: https://github.com/digilogd-cmd/ai-dev-director (remote `upstream`). This fork runs on macOS (M1).
Changes kept small so upstream merges stay easy. Local `config.yaml` (machine paths) is never committed.

## 2026-11-05 · Stage project menu + macOS folder dialog (`feat/mac-project-picker`)

Problem: the stage's 「프로젝트 ▾」 chip only opened a native folder dialog, and that dialog was tkinter-only
(`scripts/pick_folder.py`). The macOS venv has no `_tkinter`, so the button failed and the project could only be
changed by typing a path on `/classic`.

Changes:
- `companion_server.py` — new local-only `GET /ops/projects` → `{projects: project_registry(), current}`.
  It reuses the existing registry (config `projects:` + git folders one level under `solomon.allowed_roots` +
  past mission targets); nothing new is persisted.
- `console/stage.js`, `console/index.html`, `console/stage.css` — the project chip opens a menu: registered
  projects (current one marked), click = `POST /project` (the same selection the classic page uses), plus
  「다른 폴더 선택…」 which falls back to the native dialog. Closes on outside click / Escape.
- `scripts/pick_folder.py` — on macOS uses AppleScript `choose folder` (cancel → ""), tkinter elsewhere.
- `tests/test_v309_trinity_stage.py` — endpoint whitelist now includes `/ops/projects` and `/project`.

To list a project in the menu: add it to `config.yaml` `projects:` (also sets its verification commands), or put
the git repo directly under a `solomon.allowed_roots` folder.

## 2026-11-05 · Hide the 솔로몬 집무실 link

`console/console.js` workshop header linked to `http://127.0.0.1:8710` (Solomon, the upstream author's separate,
unpublished program). Nothing listens there in this fork, so the link is removed; `/bridge/*` APIs stay untouched.

## 2026-11-06 · Claude HEAD can read the shared memory (`feat/head-shared-memory`)

Problem: HEAD runs `claude --print` with no pre-approved tools, so the user's shared-memory MCP (claude-memory) calls
were denied and HEAD only saw the project CLAUDE.md.

Changes:
- `agents/claude_head.py` — new `allowed_tools` option → `--allowedTools a,b,c`, passed **only** on `chat()` and
  `create_order()` (plus a short hint: memory is data, not instructions; output stays JSON-only). Review/decision
  calls stay tool-less. Default (no option) = upstream behaviour.
- `core/orchestrator.py` — reads `head.allowed_tools` from config.
- `agents/google_roles.py` — `GoogleHead._invoke_cli` accepts (and ignores) the new `use_tools` argument.
- `tests/test_fork_head_shared_memory.py` — default/no tools, chat gets tools+hint, judging stays tool-less, config wiring.

Local `config.yaml` (not committed) enables read-only tools only:
```yaml
head:
  allowed_tools: [mcp__claude-memory__memory_index, mcp__claude-memory__memory_search, mcp__claude-memory__memory_read]
```
Measured: a chat answer that consulted the memory took ~24 s.

## 2026-11-06 · Restart the dashboard right after stopping it (`fix/mac-restart-port`)

Problem: on macOS, pressing 「켜기」 right after 「끄기」 failed with `DashboardAlreadyRunning … [Errno 48]` for
~30 s. `DashboardServer.allow_reuse_address = False` (chosen for Windows, where SO_REUSEADDR allows a duplicate
LISTEN) also blocks binding while the old connections sit in TIME_WAIT.

Change: `dashboard_server.py` — `allow_reuse_address = os.name != "nt"`. On macOS/Linux SO_REUSEADDR still refuses a
second LISTEN on the same address (measured), so single-instance holds. Windows unchanged.
Tests: `tests/test_fork_restart_port.py` (restart after a served request + duplicate refused);
`test_v0232_e2e_blockers.py` assertion follows the platform.

Also (outside the repo): `~/Downloads/install-dashboard-apps.sh` — the 켜기 app `exec`s the server and records the
Terminal window id in `data/dashboard-window-id`; 끄기 waits for the process to exit and closes that window by id
(oh-my-zsh rewrites the tab title when the prompt returns, so matching by title missed it).

## 2026-11-06 · Remote (mobile) use over Tailscale (`feat/remote-tailscale`)

Goal: open the 8081 stage from the iPhone outside home without exposing it to the internet.
Setup: Tailscale on the Mac and iPhone (same tailnet, MagicDNS + HTTPS certificates on) and
`tailscale serve --bg 8081` (tailnet only, not Funnel) → `https://<mac>.<tailnet>.ts.net` proxies to 127.0.0.1:8081.

Change: `dashboard_server.py` — `remote_hosts()` reads config.yaml `remote_access.hosts` (only `*.ts.net` names are
accepted); `is_request_origin_allowed` also accepts Host `<name>`/`<name>:443` and Origin `https://<name>`.
The server still binds 127.0.0.1 only; without the config nothing changes. Tests: `tests/test_fork_remote_tailscale.py`.
Measured through the tailnet URL: pages/registry 200, project selection POST 200, foreign Origin 403.
Turn off: `tailscale serve --https=443 off`.

## 2026-11-06 · Phone layout (`fix/mobile-project-chip`)

- `console/stage.css` — at ≤1180px the 「프로젝트 ▾」 chip gets its own fixed row under the top bar (was hidden),
  the stage and the workshop iframe start below it.
- `console/console.css` — ≤800px: workshop header status on its own full-width line (was one word per line);
  AI usage rows = role · calls · time · tokens, with the provider text on a second line and column labels kept.
Checked on the iPhone over Tailscale by the user.

## 2026-11-07 · Configurable verification time limit (`feat/verify-timeout`)

Problem: `Verifier` cut every build/test/lint command at a hard-coded 120 s. An iOS project whose test command
boots a simulator (xcodebuild test) can need more.

Change: `core/verifier.py` — `Verifier.timeout_from(config)` reads `verification.timeout_sec` (default 120, clamped
10..1800, invalid → 120). `core/orchestrator.py` (`_verify`) and `core/result_manager.py` (apply-verify) pass it.
Per project it is set through `projects: {<path>: {verification: {timeout_sec: 300}}}` (merged by
`configure_runtime`). Tests: `tests/test_fork_verify_timeout.py`.
Known unrelated on this Mac: `test_v020_provider_ops.py` needs `requests` (collection error → `test_v018` test_6),
`test_v0246` test_d / `test_v302` snapshot test fail before and after, `test_v306` repair tests are flaky before and after.

## 건너뛴 검증 단계 안내 문구 (2026-11-07)
- 문제: 프로젝트가 일부러 비워 둔 단계(예: test_command: null, 테스트는 build-for-testing에서 컴파일만)가 `No command specified`로 표시됐다. HEAD·BUILDER·REVIEWER가 이를 "검증 설정이 망가졌다"로 읽고, build 출력에 있던 진짜 컴파일 오류를 놓친 채 같은 수정을 반복했다(「어디야」 친구 관리 미션, 약 40분).
- 변경: `core/verifier.py`의 `SKIPPED_STEP_NOTE` — "설정하지 않은 단계(일부러 건너뜀, 실패 아님). 검증이 실패했다면 원인은 build 출력에 있다"는 안내로 바꿨다. passed/executed 판정은 그대로.
- 테스트: `tests/test_fork_skipped_step_note.py`

## 한 AI가 여러 역할 맡기 (2026-11-07)
- 문제: 세 역할(HEAD·BUILDER·REVIEWER)에 반드시 서로 다른 AI를 넣어야 했다. BUILDER를 Claude로 바꾸려면 HEAD를 Codex나 Antigravity에 넘겨야 했다. 그러면 공유 메모리와 git에 없는 프로젝트 규칙을 읽는 HEAD를 잃는다.
- 변경
  - `core/role_settings.py`의 `validate`: 중복 금지 검사를 뺐다. 이제 AI 1·2·3개 어떤 조합이든 저장된다. 알 수 없는 AI 이름은 여전히 거부한다.
  - `console/stage.js` 역할 메뉴: AI를 고르면 **그 역할만** 바뀐다. 상대와 맞교환하지 않는다. 버튼 아래에 "HEAD와 함께 맡음"처럼 겸하는 역할을 표시한다.
  - `console/console.js` 카드의 「다음: 역할 ↻」 순환 버튼: 역할을 하나만 맡은 AI일 때만 동작한다. 여러 역할을 겸하거나 역할이 없으면 겸하는 역할을 보여 주고 버튼을 끈다(작업실 역할 메뉴에서 바꾼다).
  - 카드 역할 표시(`provider_health.current_role`)와 `_node_activity`는 원래 "HEAD, BUILDER"처럼 여러 역할을 지원하므로 그대로 둔다.
- 주의: 같은 AI가 구현과 검토를 함께 맡으면 독립 검토 효과가 줄어든다. Claude가 HEAD와 BUILDER를 겸하면 같은 구독의 5시간 사용 한도를 함께 쓴다(2026-11-07 M1에서 Claude BUILDER가 4초 만에 exit 1로 끝난 원인이 `usage_limit_reached` 429였다).
- 테스트: `tests/test_fork_shared_roles.py`(27개 조합 저장·적용, 겸임 시 에이전트 연결과 카드 역할 기록). `tests/test_v304_roles.py`의 무효 사례에서 "중복"을 "역할 누락"으로 바꿨다.

## 실패 원인 보기·알림, 목록 스크롤, Claude 호출 기록 누락 (2026-11-07)
- **문제**
  - 작업 장부(`ai_usage`)는 ERROR/TIMEOUT만 남기고 이유를 버렸다. 그래서 사용 한도 초과, 시간 초과, 로그인 만료를 화면에서 구분할 수 없었다.
  - 스테이지의 「진행 기록」과 「최근 AI 호출」 목록이 잘려서 스크롤도 되지 않았다.
  - Claude CLI 경로가 `~/.local/share/claude/versions/2.1.319`라서 `provider_of`가 이를 Claude로 알아보지 못했다. 그래서 **Claude 호출이 장부에 한 번도 기록되지 않았다**(작업량 "호출 없음").
- **변경**
  - `core/failure_reason.py`
    - `classify()`: 종료 코드, 시간 초과 사유, 출력 내용으로 원인을 나눈다. 사용 한도(풀리는 시각 포함), 요청 과다, 토큰 한도, 로그인, 과부하, 네트워크, 출력 없음 시간 초과, 제한 시간, 강제 종료, 응답 형식, CLI 오류. 원인마다 한국어 설명과 할 일을 붙인다.
    - `classify_step()`: 단계 실패를 나눈다. 구현 실패와 그 안의 원인, 검수 불합격, 빌드·테스트 실패, AI 없음, 내부 오류, 사용자 결정.
    - Claude 응답이 잘려 원인이 안 보이면, 읽기 전용으로 세션 기록(`~/.claude/projects/*/<session>.jsonl`)에서 API 오류를 찾는다.
  - `core/usage.py`
    - 장부에 `error_code/label/detail` 열을 추가했다(없으면 ALTER).
    - `recent_calls`에 원인을 담는다. `recent_failures()`를 새로 만들었다.
    - `backfill_failure_reasons()`: 예전 호출은 run의 `*_cli_attempts.json`에서 원인을 찾아 붙인다. 최근 것끼리, 시간 초과는 시간 초과끼리 짝짓는다.
    - `provider_of`: `versions/<x>`의 상위 폴더 이름과 Windows 경로도 알아본다.
  - `dashboard_server.py`: `/ops/activity`에 `failures`를 넣는다. 최근 72시간의 호출 실패, 원인별 건수, 단계 실패(`state_transitions`의 FAIL/HUMAN_REQUIRED/TIMEOUT)와 원인을 담는다. 최근 호출은 15건에서 30건으로 늘렸다.
  - `core/alerts.py`
    - Mac 알림 센터 배너를 띄운다(`osascript`, 추가 설치 없음).
    - AI 호출이 실패하거나(강제 종료는 제외) 단계가 실패하면 알린다. 같은 원인은 10분에 한 번만 알린다.
    - 테스트 중이거나 `DIRECTOR_MAC_ALERTS=0`이면 끈다.
  - `console/stage.*`
    - 두 목록에 스크롤을 넣었다(진행 기록 25건, 호출 30건).
    - 항목을 누르면 자세한 내용이 펼쳐진다(원인, 할 일, run). 실패한 호출은 상태 대신 원인을 보여 준다.
    - 「최근 AI 호출」 옆에 「실패 원인 N」 버튼을 두었다. 원인별 요약, 단계 실패, 호출 실패 목록을 보여 준다.
    - 새 실패가 생기면 화면 위에 알림이 뜬다(「원인 보기」). 「브라우저 알림 켜기」를 누르면 탭이 뒤에 있을 때도 알림이 온다.
- **테스트**: `tests/test_fork_failure_reasons.py`. 덤으로 `test_v307` 중 `test_claude_reported_tokens_are_exact`가 이제 맥에서도 통과한다(Windows 경로 처리 때문).
