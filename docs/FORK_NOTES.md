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
- **추가(같은 날): Discord 알림**
  - `core/alerts.py`가 Mac 배너와 같은 조건(같은 원인은 10분에 한 번)으로 Discord 채널 웹후크에도 메시지를 보낸다. 별도 스레드에서 보내며, 시간 제한은 10초다.
  - 웹후크 주소는 비밀 정보다. 그래서 `config.yaml`의 `alerts.discord_webhook`이나 `DIRECTOR_DISCORD_WEBHOOK` 환경 변수에서만 읽는다. `https://discord.com/api/webhooks/<id>/<token>` 형식만 받는다.
  - 메시지는 멘션을 끈 채로 보낸다(`allowed_mentions` 비움).

## 스테이지 역할 표시 수정, 구독 남은 한도 표시, 수동 반영 상태 동기화 (2026-11-07)
- **역할 표시 버그**
  - 문제: 작업이 진행 중이면 우측 역할 칸이 `/status`의 `providers.role`을 읽는다. 겸임이면 이 값이 `"HEAD, BUILDER"`처럼 와서 알아보지 못했고, BUILDER가 기본값(Antigravity)으로 표시됐다.
  - 수정: `console/stage.js` `assignment()`가 이 값을 쉼표나 「·」로 나눠 읽는다.
- **구독 남은 한도**: 「AI별 작업량」 패널에 접을 수 있는 섹션을 추가했다. 엔드포인트는 `GET /ops/limits`(`companion_server.py`), 코드는 `core/subscription_limits.py`.
  - Claude: `claude --print --output-format stream-json --verbose`가 내보내는 `rate_limit_event`를 쓴다. 5시간·주간 사용률과 초기화 시각이 들어 있고, VS Code 확장도 같은 정보를 쓴다. Haiku로 짧은 확인 호출을 한다. 대시보드를 열어 둔 동안에만, 기본 10분마다, 백그라운드로 실행하며 작업 장부에는 기록하지 않는다.
  - Codex: `~/.codex/sessions/**/rollout-*.jsonl`의 `payload.rate_limits`를 읽는다. 추가 호출은 없다.
  - Antigravity: `agy -p /usage --output-format json`을 쓴다. 로컬 명령이라 토큰을 쓰지 않는다. 모델 그룹별 주간 한도(남은 비율, 초기화 시각)를 받는다. 실패하면 장부에 남은 마지막 사용 한도 초과 기록을 보여 준다.
  - 결과는 `data/subscription_limits.json`에 저장한다. 확인 간격은 `config.yaml`의 `subscription_limits.claude_probe_minutes` / `antigravity_probe_minutes`로 정한다(기본 10, 0이면 끔).
  - 화면
    - 제목을 누르면 접고 펼친다. 상태는 localStorage에 기억한다.
    - 접으면 AI별 최대 사용률을 한 줄로 요약한다. 폭이 좁으면 이름 대신 색 점을 쓴다.
    - 툴팁은 CSS `data-tip`으로 그린다. 일부 Chromium 계열 브라우저는 button 안의 title을 보여 주지 않는다.
  - 예전 서버에는 `/ops/limits`가 없어 404가 오는데, 그때는 섹션을 숨긴다.
- **수동 반영 → 결과 상태**
  - 문제: Claude Code가 `git apply`로 반영하고 미션 JSON에 `applied`를 적으면 `result_state`가 READY로 남는다. 그래서 「작업실」 배지와 "적용 대기 N건"이 계속 떠 있었다.
  - 수정: `ResultManager.sync_manual_applies()`(`pending_results()`가 호출)가 처리한다. `applied.status == "applied"`인 미션에서 반영된 run까지의 PASS 단계를 APPLIED로 바꾸고, 감사 기록 `RESULT_APPLIED_MANUAL`을 남긴다. 작업본이 없으면 `worktree_state`를 MISSING으로 둔다.
- **테스트**: `tests/test_fork_subscription_limits.py`. `test_v309` 엔드포인트 목록에 `/ops/limits`를 추가했다.

## 고치기 반복 속도: 컴파일 오류 전달, 컴파일 즉시 수정, 진전 없음 중단, 고칠 부분만 수정 (2026-11-08)
- **배경**: 어디야 F2 미션(RUN-261108005514)이 1시간 23분 걸리고 FAIL로 끝났다. 사용량 한도 문제를 빼고 보면 원인은 다섯 가지였다.
  1. 빌드 로그(50KB)의 끝부분만 넘어가서 실제 컴파일 오류 줄이 아무에게도 전달되지 않았다.
  2. 컴파일되지 않는 코드도 Reviewer(약 12분)와 HEAD 판정을 거쳤다.
  3. 고치기 라운드에서 Builder가 아무것도 바꾸지 않았는데도(필요한 파일이 허용 범위 밖) 검증·검토·판정을 두 번 더 돌았다.
  4. 시간 초과한 제공자에게 다시 맡겨 10분을 또 썼다.
  5. 고치기 라운드마다 프로젝트 전체(약 19만 자)와 "ORDER를 구현하라"는 지시를 다시 보냈다. 그래서 Builder가 매번 처음부터 다시 작업했다.
- **수정**
  - `core/verifier.py` `error_digest()`: 빌드가 실패하면 컴파일러 오류 줄(`파일:줄: error:`, tsc, Python 예외)만 뽑아 `verification.build_errors`에 넣는다. 작업본 경로는 떼어 내고 중복은 없앤다.
  - Reviewer 2종(`BUILD ERRORS`)과 HEAD 2종(`build_errors`), HEAD 고치기 지시(`HEAD REPAIR ORDER.build_errors`)에 이 목록을 넣는다.
  - `core/orchestrator.py` `_compile_fix_rounds()`: 빌드가 실패하고 오류 줄이 있으면 Reviewer/HEAD를 건너뛰고 `COMPILE FIX ORDER`로 바로 Builder에게 돌려보낸다. 횟수는 `autonomy.compile_fix_rounds`(기본 2, 0이면 끔)로 정하며 `max_repairs`와 따로 센다. 상태 전환표에 VERIFYING → BUILDING을 추가했다.
  - 고치기 라운드에서 파일 내용이 그대로이고 직전 검증도 실패했으면 `REPAIR_NO_PROGRESS`로 바로 FAIL한다. 그러면 미션 계획 단계가 다시 계획한다. 검증이 통과한 상태라면 예전처럼 다시 검토한다.
  - Builder가 TIMEOUT으로 실패하면 같은 제공자로 다시 시도하지 않고 바로 다른 제공자로 넘긴다.
  - `agents/proposal_builder.py` 고치기 모드: context에 `HEAD REPAIR ORDER`나 `COMPILE FIX ORDER`가 있으면 다음만 보낸다.
    - 고칠 목록: 컴파일 오류, HEAD next_steps, 검토 P0/P1. P2와 테스트 출력은 뺀다.
    - 관련 파일 최대 12개: 오류 줄에 나온 파일 → 이름이나 CamelCase 타입 이름이 언급된 파일 → 이번 작업에서 바꾼 파일. 허용 범위 밖 파일은 읽기 전용으로 보낸다.
    - 오류 줄 앞뒤 10줄(줄 번호 포함).
    - "다시 만들지 말고 이것만 최소 수정" 지시.
  - 3,000자가 넘는 기존 파일을 통째로 다시 쓰면 `REPAIR_REWRITE`로 거부한다. 관련 파일을 찾지 못하면 예전처럼 전체 프롬프트를 보낸다. F2 기록 기준으로 프롬프트가 21.4만 자에서 1.3만~6.1만 자로 줄었다.
  - Builder가 참고로 읽는 파일 종류에 `.swift`·`.kt`·`.go`·`.rs` 등을 추가했다. 예전에는 허용 범위 밖의 Swift 파일이 Builder에게 보이지 않았다.
  - 기존 버그: `_dirty_state()`는 `_run_git`이 strip한 첫 줄을 `line[3:]`로 잘라서 파일 이름의 첫 글자를 잃었다(`feature.py` → `eature.py`).
- **테스트**: `tests/test_fork_repair_speed.py`(17개). `test_v303`의 always_wrong 경우는 빌드 횟수 기대값을 3에서 2로 바꿨다(진전 없음 중단).

## 토큰·대기 줄이기: 가벼운 Claude 호출, Builder 시간 초과 재시도 중단, 첫 코딩 프롬프트 좁히기 (2026-11-08)
- **배경**: 2026-11-01~08 사용량 장부를 점검했다.
  - Antigravity Builder는 43번 중 28번이 시간 초과로, 163분을 썼다.
  - Claude HEAD는 보낸 글자 수보다 토큰을 몇 배씩 썼다(판정 6.8만 자 → 70만 토큰, 계획 1.4만 자 → 14만 토큰).
  - 첫 Builder 프롬프트는 15만~21만 자였다.
- **가벼운 Claude 호출** (`core/claude_cli.py`)
  - Director가 부르는 `claude --print`에 `--strict-mcp-config --disable-slash-commands --setting-sources "" --tools <...>`를 붙인다.
  - 효과: 사용자가 등록한 MCP 서버 전부(도구 정의 수백 개), 스킬, iTerm 훅, CLAUDE.md의 "먼저 메모리를 읽어라" 같은 지시를 따르는 여러 턴 탐색이 사라진다. Haiku 한 줄 기준으로 기본 실려 가는 양이 3.8만 토큰에서 4천 토큰으로 줄었다.
  - HEAD 계획·대화(`use_tools`)에는 allowed_tools가 가리키는 MCP 서버(공유 메모리)만 담은 임시 `--mcp-config`를 넘긴다. 값은 `~/.claude.json`에서 복사하고, 파일은 0600으로 만들어 호출이 끝나면 지운다. 실측: 메모리를 읽는 호출이 2턴, 1.4만 토큰.
  - 도구 설정: HEAD 판정·Builder는 도구 없음, Claude Reviewer는 `Read,Grep,Glob`만.
  - 끄는 법: `claude_cli.lean: false` 또는 `DIRECTOR_CLAUDE_LEAN=0`. 구독 한도 확인 호출은 원래 가볍게 부르고 있었다.
- **Builder 시간 초과 재시도 중단** (`core/research_runner.py`): BUILDING 단계의 TIMEOUT은 같은 프롬프트로 다시 보내지 않는다(예전에는 최대 2×600초). 이제 Director가 바로 다른 제공자로 넘긴다. 다른 단계의 1회 재시도는 그대로다.
- **첫 코딩 프롬프트** (`agents/proposal_builder.py`)
  - 전문을 보내는 파일
    - ORDER(목표·context·수용 기준)가 경로나 파일 이름을 댄 파일
    - 허용 범위 패턴 하나에 4개 이하로 걸리는 파일
  - 관련 파일(4만 자 안에서)
    - 위 파일들과 ORDER가 CamelCase 타입 이름으로 쓰는 파일
    - 이름이 나온 참고 문서
  - 나머지는 "OTHER FILES IN SCOPE"로 경로만 알린다. 이름이 나온 파일이 없으면 예전처럼 보낸다.
  - 프롬프트에서 늘 빼는 것: 바이너리, `.xcassets`·`.xcodeproj` 등 번들 폴더, 잠금 파일, CLAUDE.md·AGENTS.md·GEMINI.md(서버 주소 같은 비밀 정보가 다른 제공자로 가지 않게)
  - F2 기준 첫 코딩 프롬프트가 21.2만 자에서 10.9만 자로 줄었다. 지난번 실패 원인인 `RootTabView.swift`가 처음부터 들어간다.
- **테스트**: `tests/test_fork_lean_calls.py`(14개). 바뀐 동작에 맞춰 `test_v300c` 9(Claude 명령에 옵션이 붙음)와 `test_v308`(넘치는 파일은 경로 목록으로)의 기대값을 갱신했다.

## DB 연결 닫기: 오래 켜 둔 서버가 빈 응답을 보내던 문제 (2026-11-09)
- **증상**: companion 서버를 12시간쯤 켜 두자 대시보드 첫 화면과 CSS 요청에 빈 응답이 왔다(파일을 읽지 않는 404 경로는 응답했다). 서버는 `data/director.db`를 247번 연 채로 있었고, 열린 파일이 295개로 프로세스 한도 256개를 넘었다.
- **원인**: 모든 DB 접근이 `with sqlite3.connect(...) as conn:` 형태였다. 이 형태는 트랜잭션만 끝내고 연결은 닫지 않는다. Python 3.14에서는 연결이 순환 참조로 남아 GC가 돌 때까지 파일 핸들이 열려 있다(GC를 끄고 20번 부르면 20개가 남는다).
- **수정** (`core/db_path.py`): `ClosingConnection`과 `connect(path, **kw)`를 추가했다. `with` 블록이 끝나면 커밋(오류면 롤백)한 뒤 연결을 닫고, 결과는 `sqlite3.Row`로 돌려준다. 연결을 만드는 7곳(state_machine, chat_manager, provider_manager, research_state, usage, solomon_bridge, dashboard_server)이 모두 이 함수를 쓴다.
- **테스트**: `tests/test_fork_db_close.py`(2개). 블록이 끝나면 닫히는지, 오류 때 롤백하는지, GC를 꺼 둔 채 60번 불러도 핸들이 남지 않는지 확인한다.

## macOS 메뉴 막대 앱과 서버 자동 실행 (2026-11-09)
- **목적**: 터미널 창 없이 Director 서버를 로그인 때 켜고, 켜져 있는지 보고, 켜기·끄기·로그 보기를 메뉴 막대에서 한다.
- **설치**: `tools/macos/install.sh`(다시 실행하면 갱신, `uninstall`로 제거). 실행한 셸의 PATH를 서버 LaunchAgent에 적어서 서버가 claude/codex/agy를 찾게 한다.
  - `~/Library/LaunchAgents/local.ai-dev-director.server.plist`: `tools/macos/run_companion.sh` 실행. 로그인 때 시작하고, KeepAlive가 없어서 끄면 꺼진 채로 남는다.
  - `~/Library/LaunchAgents/local.ai-dev-director.menu.plist`: `~/Applications/Director 메뉴.app` 실행.
- **run_companion.sh**: 로그 `logs/companion_server.log`(10MB를 넘으면 `.1`로 하나 보관), `PYTHONUNBUFFERED=1`, 실행마다 시작 시각 한 줄.
- **메뉴 막대 앱** (`tools/macos/DirectorMenu.swift`, Xcode 프로젝트 없이 swiftc로 빌드)
  - 아이콘: 초록 = 실행 중이고 응답함, 주황 = 실행 중인데 `/status`가 응답하지 않음, 회색 = 꺼짐. 10초마다 다시 확인한다.
  - 메뉴: 상태(PID·메모리), 대시보드 열기, 켜기·끄기·다시 시작(끄기와 다시 시작은 확인 창을 띄움), 최근 로그 20줄, 로그 보기(콘솔), 로그 폴더 열기.
  - 켜기·끄기는 `launchctl kickstart` / `launchctl kill SIGTERM`. 서버는 켠 뒤 15초쯤 지나야 응답한다.

## 「원본에 적용」 한 번으로 검수·적용·마무리까지 (2026-11-09)
- **목적**: 미션이 PASS로 끝난 뒤 Claude Code가 손으로 하던 일(로직 테스트, 문구 카탈로그 확인, 적용, ⌘U 확인, 커밋, graphify, push·개발일지, 작업본 정리, 공유 메모리·CLAUDE.md 기록)을 [원본에 적용] 한 번으로 Director가 한다. 2026-11-08에 치워 둔 stash(apply-pipeline)를 되살려 이어 만들었다.
- **켜는 법**: `config.yaml` → `projects.<경로>.apply_pipeline`. 블록이 없는 프로젝트는 예전 그대로(복사·빌드·실패 시 되돌림만) 동작한다. 단계마다 따로 켜고 끈다. 설정 예시는 `core/apply_pipeline.py` 맨 위 설명에 있다.
- **순서**: 적용 전 검수(`prechecks`, 작업본에서) → 문구 카탈로그 점검(`string_catalog`) → 원본에 적용·빌드 확인 → 적용 뒤 확인(`checks`, 원본에서) → 사용자 확인(`manual_checks`, 예: ⌘U) → git 커밋 → graphify 갱신 → push → 개발일지 초안 → 작업본·브랜치 정리 → 공유 메모리·CLAUDE.md 기록
- **실패하면**
  - 적용 전 검수·카탈로그(`fail_on`) 실패: 원본은 건드리지 않고, 실패 출력을 마지막 단계 결과(`post_apply_failure`, "PRE-APPLY CHECK FAILED")에 붙여 HEAD에 재작업을 맡긴다(「이어서 진행」과 같음).
  - 적용·적용 뒤 확인·⌘U [실패]: 적용 백업으로 원본을 되돌리고(`revert_applied`) 같은 방식으로 재작업을 맡긴다.
  - 충돌로 적용 보류: 재작업 없이 사용자를 기다린다(BLOCKED).
  - 커밋 뒤 단계(커밋·graphify·push·개발일지·정리·기록) 실패: 되돌리지 않고 경고로 남긴다(완료 · 경고 있음).
- **단계별 내용**
  - `prechecks`: 작업본에서 실행한다. 명령의 `{worktree}`·`{project}`를 바꿔 넣고 환경변수 `DIRECTOR_WORKTREE`·`DIRECTOR_PROJECT`를 준다. 명령이 작업본 파일을 바꾸면 실패로 본다(빌드 산출물이 적용에 섞이지 않게).
  - `string_catalog` (`core/string_catalog.py`): 빌드가 만든 `*.stringsdata`(같은 derivedDataPath를 원본·작업본이 함께 쓰므로 `source`가 작업본 안인 것만) 키와 `Localizable.xcstrings`를 비교한다. 카탈로그에 없는 키, stale 표시·코드에서 안 쓰는 키, 바뀐 Swift 줄의 삼항식 안 한국어 문구를 보고한다. 기본은 보고만 하고(⚠), `fail_on: [missing|stale|ternary]`이면 실패로 본다. stringsdata가 없으면 프로젝트 빌드 명령을 작업본에서 한 번 돌린다(`build_if_missing`).
  - 여러 단계 미션: 마지막 PASS 작업본과 처음 단계의 기준 커밋(`origin_ref`)으로 전체 차이를 적용한다(기존 `apply_tree`). 작업본에서 지운 파일과 충돌로 건너뛴 파일은 적용 단계에 ⚠로 표시한다.
  - 커밋: 적용한 파일만 하나씩 지정해 add한다. 무시 대상과 `CLAUDE.md`는 넣지 않고, git이 CLAUDE.md를 추적 중이면 경고한다. `include_if_changed`(예: ⌘U 때 Xcode가 다시 쓰는 `Localizable.xcstrings`)는 적용 절차 시작 때 변경이 없던 파일만 함께 커밋한다(사용자의 미커밋 수정은 넣지 않음). 메시지: 제목 = 미션 목표 첫 줄(72자), 본문 = 요약·미션 ID·결과 run·통과한 확인, 끝에 `Co-Authored-By`(`commit.co_authors`).
  - graphify: `graphify-out/graph.json`이 있을 때만 `graphify update .`.
  - push: 원격과 추적 브랜치가 있을 때만. 개발일지는 push가 된 뒤에만 `ssh -o BatchMode=yes <ssh_host> ./devlog <repo> <커밋 제목>`(인자는 원격 셸용으로 따옴표 처리).
  - 기록: HEAD Claude(`claude --print`, 가벼운 호출)에 이 호출에서만 `memory_append`를 더 연다. 허용 도구는 HEAD의 메모리 읽기 도구 + `memory_read`·`memory_append` + `Read/Edit(./CLAUDE.md)`이고, `memory_write`·`memory_delete`·Bash·Write는 막는다. 호출 뒤 git 추적 파일이 바뀌었거나 CLAUDE.md의 `## 작업 현황 (handoff)` 섹션 밖이 바뀌었으면 실패로 보고 CLAUDE.md를 되돌린다.
- **대시보드**: 스테이지 결과물 카드에 단계 목록(○ 대기 · ◐ 진행 · ✓ 통과 · ⚠ 확인 필요 · ✕ 실패 · ⏳ 사용자 확인 · – 건너뜀)과 실패 사유·⚠ 보고·건너뛴 이유를 보여 준다. 사용자 확인 단계에서는 [통과]/[실패] 버튼이 나오고, [실패]에 적은 내용이 HEAD 재작업에 그대로 전달된다. 진행 상태는 미션 JSON의 `delivery`에 있어 서버를 다시 켜도 사용자 확인 대기가 남는다.
- **테스트**: `tests/test_fork_apply_pipeline.py`(6개, stash에서 되살림), `tests/test_fork_apply_pipeline_ext.py`(새 단계).

## 실패 원인 창을 한국어로 (2026-11-09)
- **증상**: 「실패 원인」 창의 작업 단계 실패 내용이 영어였다("Final decision: Overridden by Orchestrator: Reviewer reported existing behavior loss or P1 concerns: …"). 게다가 Director가 PASS를 뒤집을 때 리뷰어의 **요약**만 적어서, 정작 어떤 P1 지적 때문에 불합격인지 보이지 않았다(요약은 "요청한 세 가지는 반영됨"처럼 통과처럼 읽혔다).
- **새 판정부터**
  - 리뷰어(Claude·Codex·Antigravity) 프롬프트에 `REVIEW_LANGUAGE_RULE`: p0/p1/p2 항목과 summary를 한국어로 쓴다(코드 이름·경로·테스트 이름·오류 메시지는 그대로, JSON 키와 verdict 값은 영어).
  - HEAD 최종 판정(Claude·Codex) 프롬프트에 `DECISION_LANGUAGE_RULE`: rationale과 next_steps를 한국어로 쓴다(맨 앞 `USER_DECISION:` 표시는 영어 유지 — 자동 판정이 이 표시를 본다).
  - Director가 PASS를 뒤집는 이유를 한국어로 쓰고, 리뷰어 요약 대신 실제 지적을 적는다: `P1(꼭 고칠 문제): …` / `삭제 관련 지적: …` / `P2(작은 문제): …` / `리뷰어 요약: …`.
  - 삭제 경고 판별(`_mentions_deletion`)이 영어 remove/delet와 함께 한국어(삭제·제거·지워·없앴·사라 등)도 본다. 리뷰어가 한국어로 써도 기존 동작 삭제 지적이 PASS를 막는다.
  - 상태 기록의 맨 앞 "Final decision:"과 "P2 FOLLOW-UPS"는 그대로 둔다(분류·테스트가 이 표시를 쓴다).
- **이전 기록**: `failure_reason.to_korean()`이 Director가 붙이는 고정 문구(최종 판정, 판정 뒤집음, P0/P1 보고, 사용자 결정 필요, 구현 AI 실패, 빌드 통과 등)를 화면에 보일 때 한국어로 바꾼다. AI가 쓴 영어 문장은 그대로 두고, 그런 기록에는 "AI가 영어로 남긴 기록입니다" 안내를 붙인다(`classify_step` → `detail_ko`, `english`).
- **테스트**: `tests/test_fork_korean_failures.py`(5개).

## P2 지적이 PASS를 FAIL로 뒤집던 판정 버그 (2026-11-10)
- **증상**: 어디야 F11 미션 MSN-261109224303(6단계)에서 1·2·4단계가 빌드 PASS, 리뷰어 verdict 'pass'(p0·p1 없음), HEAD PASS인데도 FAIL이 됐다. HEAD가 사소한 P2 지적을 고치는 단계를 계속 만들어 단계 예산 6을 거의 다 썼다.
- **원인**: `apply_final_pass_gate`의 삭제 경고가 P1·P2 문장에 "remove"/"delet" 글자가 있는지만 봤다. "`intro` was removed … so it is not a forbidden deletion" 같은 P2 메모, `removePersistentDomain` 같은 함수 이름도 삭제 경고가 됐다(2026-11-09 한국어화 때 한국어 단어까지 넣어 더 넓어졌던 것도 함께 되돌림).
- **수정**
  - 리뷰 스키마에 `forbidden_deletions: string[]`(기본 빈 목록, 예전 review.json도 그대로 읽힘)를 두고, 리뷰어 프롬프트(Claude·Codex·Antigravity)가 "ALLOWED DELETIONS 허락 없이 지운 기존 함수·클래스·테스트·동작만" 적게 한다. 삭제 경고는 이 목록이 비어 있지 않을 때만 켜진다. P0/P1 지적은 예전처럼 그 자체로 PASS를 막는다.
  - 리뷰가 pass이고 P2만 있으면 판정을 바꾸지 않고 `[P2 FOLLOW-UPS: …]`로 기록만 한다.
  - 판정 뒤집기 이유의 "삭제 관련 지적"은 "허락 없이 지운 것"(forbidden_deletions)으로 바꿨다.
- **테스트**: `tests/test_fork_p2_gate.py`(F11 1단계 review.json 형태에서 PASS 유지 등 5개), `test_fork_korean_failures.py`의 삭제 테스트를 플래그 기준으로 갱신.

## 디스코드 접수함 (2026-11-12~13)
- OpenClaw(TrueNAS VM)가 디스코드 요청을 맥에 **접수만** 하고, 미션 시작은 사람이 Director 화면에서 [시작]을 누른다.
- `tools/director-intake.py`: SSH forced command(`authorized_keys`의 `command=…,restrict`)로만 쓰는 명령. projects·submit·status·list + 읽기 전용 files·read·search(git이 추적하는 파일만, CLAUDE.md·.env·비밀 이름 차단, 비밀값 가림). 요청 5~4000자, 비밀값 거절, 대기 10개·하루 20개, 같은 요청 중복 방지.
- `core/discord_intake.py` + companion `/ops/intake`·`/ops/intake/start`·`/ops/intake/discard`(로컬 Origin + X-Solomon-Bridge), 콘솔 「디스코드 접수함」과 맨 위 「디스코드 접수 N」 버튼. start는 허용 프로젝트를 다시 확인하고 `start_goal_mission(source='discord')`.
- `tools/setup-intake-key.sh`: VM 열쇠를 접수 전용으로 묶는다.
- 테스트: `tests/test_discord_intake.py`(9개).

## 한도 일시정지·Swift 테스트 집계·목표 이탈 막기 (2026-11-13)
- **계기**: 어디야 F6 미션 MSN-261113131013(5단계 FAILED). 1단계 코드는 좋았지만 테스트 미설정으로 Codex 리뷰·판정이 "테스트 증거 없음" FAIL, HEAD가 F6 대신 테스트 스크립트를 고치는 단계를 세움, Claude 5시간 한도를 UNKNOWN으로 보고 대체 Builder가 빈 결과.
- `research_runner._claude_error_text`: `claude --print` JSON 끝의 `result`(한도 문구)를 오류 메시지 앞에 둔다. `ErrorCategory`가 hit your limit·5-hour limit·weekly limit을 QUOTA로 본다.
- `mission._usage_limit_stop`: 변경 없이 끝난 단계에 USAGE_LIMIT 기록이 있으면 실패로 세지 않고 BLOCKED로 멈춘다(「이어서 진행」).
- `verifier._parse_test_counts`: Swift Testing(`Test run with N tests … passed/failed`)·XCTest(`Executed N tests, with K failures`) 집계.
- HEAD 계획 "STAY ON THE MISSION GOAL" + `autopilot.verification_infra_paths/off_goal_infra/protect_infra`: 목표가 말하지 않은 검증 파일(검증 명령이 쓰는 저장소 파일, CI)을 고치는 ORDER는 HEAD에 되돌리고(OFF_GOAL), 통과 ORDER는 그 파일을 protected_paths에 넣는다.
- 테스트: `tests/test_fork_limit_pause.py`(3개), `tests/test_fork_stay_on_goal.py`(4개).

## HEAD 대화를 요청별·프로젝트별로 나눠 보기 (2026-11-14)
- **계기**: 작업실 가운데 HEAD 대화창이 모든 요청·시스템 알림을 한 줄로 계속 쌓아서, 어느 요청의 답인지, 지난 대화가 어디까지인지 찾기 어려웠다.
- `chat_messages.project_path` 열을 추가했다(ChatManager 마이그레이션). 메시지를 쓸 때 세션의 현재 프로젝트를 함께 저장한다. 예전 메시지는 `_fill_message_projects`가 PROJECT_SELECTED 알림을 따라 추정한다(세션 앞부분은 처음 알려진 프로젝트, 그것도 없으면 세션 프로젝트).
- 대화창 묶음(DASHBOARD_JS): USER 요청 하나가 접히는 묶음 하나(`#번호 · 요청 첫 줄 · 결과(✓ 완료/✗ 실패/⚠ 확인 필요/● 진행) · 프로젝트 · 시각 · 메시지 수`)가 된다. 가장 최근 묶음만 펼쳐 두고(「최근」 표시), 예전 묶음은 접힌다. 프로젝트가 바뀌거나 새 대화가 시작되면 구분 묶음이 생긴다.
- 도구 줄: [현재 대화]/[전체 기록], 프로젝트 고르기(요청 수 표시), 「요청으로 이동」, [이전 요청 접기]·[모두 펼치기]. 「전체 기록」은 새 GET `/chat/history?project=`(모든 세션, 읽기 전용, 최근 1500개)를 따로 그린다. 실시간 대화창의 폴링·추가 방식은 그대로다.
- 테스트: `tests/test_fork_chat_turns.py`(5개). `test_v0238 test_1`은 메시지가 묶음 안으로 들어가게 바뀐 것에 맞춰 확인 문구를 고쳤다.

## Rust·Kotlin·Next.js·Vite 컴파일 오류를 Builder에게 제대로 전달 (2026-11-15)
- **계기**: 대상 프로젝트를 Rust로 하는 방안을 검토하다 확인. rustc 기본 형식은 `error[E0308]: msg` 다음 줄 ` --> path:line:col`에 위치가 있어서, `error_digest`가 마지막 `error: could not compile …` 한 줄만 남겼다. Builder는 어느 파일 몇 번째 줄이 왜 틀렸는지 모른 채 고쳐야 했다.
- `verifier._RUST_ERR`·`_rust_error`: `error[Exxxx]: msg`(기본 형식)와 `path:line:col: error[Exxxx]: msg`(`--message-format=short`)를 읽는다. 기본 형식은 뒤따르는 ` --> ` 줄을 위치로 붙이고, 첫 주 레이블(`^^^ expected i32, found &str`)과 첫 `help:` 줄을 괄호로 덧붙인다. 다음 진단(error/warning)이 나오면 멈추므로 warning의 help가 섞이지 않는다.
- 결과 형식: `src/main.rs:4:18: error: E0308: mismatched types (expected `i32`, found `&str`)` (tsc의 `error: TS2322: …`와 같은 모양).
- 테스트: `tests/test_fork_repair_speed.py`에 3개 추가(기본 형식, short 형식, cargo가 있으면 실제 `cargo build`).
- 같은 날 사용자 프로젝트(iOS·macOS·웹·나중에 Android) 기준으로 다시 확인한 결과, Kotlin(Gradle)·Next.js·Vite 빌드 실패는 `build_errors`가 **빈 목록**이었다. 실제 next 14.2·vite 5.4·tsc 5.6 출력을 받아 `verifier._toolchain_error`를 추가했다.
  - Kotlin: 2.x `e: file:///abs/X.kt:12:5 msg`, 1.x `e: /abs/X.kt: (12, 5): msg`, 윈도우 `file:///D:/…`. 경고(`w:`)는 뺀다.
  - Vite: esbuild `abs:2:8: ERROR: msg`, esbuild CLI `✘ [ERROR] msg`(아래 위치 줄), Rollup `Could not resolve "x" from "src/main.ts"`, 옛 `[vite]: Rollup failed to resolve import`.
  - Next.js: `./src/app/page.tsx[:줄:칸]` 머리 줄 다음의 `Type error:`·`Module not found:`, SWC 문법 오류(`Error:` → `x msg`, `^` 위의 줄 번호). `next build`의 색 코드(ANSI)는 지운다. 머리 줄 뒤에 오류 문장이 없으면 무시한다.
  - 윈도우 작업본 경로를 `/` 형태로도 떼어 낸다.
- 테스트: 같은 파일에 8개 추가(Next 3, Vite 2, Kotlin 2, 오인식 방지 1).
- 참고: `test_v306…test_resumed_run_with_failed_verification_continues_to_repair`는 이 변경 전에도 3번 중 2번 실패하는 불안정한 테스트다(Linux에서 확인).
- 색 코드(ANSI)를 지우는 처리 덕분에 **색을 입힌 Swift 오류**도 읽는다. 예전 코드는 어디야 `scripts/logic-tests.sh`의 `path:24:21: \x1b[1;31merror: …`를 하나도 못 읽었다(맥 run 기록 확인). 테스트 1개 추가.

## 테스트 단계 컴파일 실패는 빌더 직행, 시간 초과한 HEAD는 다시 묻지 않기 (2026-11-15)
- **계기**: 맥 run 기록 분석(`data/director.db`의 ai_usage, 11-08~11-15: 호출 234번·196분·토큰 626만).
  - HEAD가 시간의 61%(120분)를 썼고, 그중 **86분이 Claude HEAD 호출 18번의 무출력 시간 초과**였다. 정상 HEAD 응답은 평균 28초·최대 93초다. 5시간 한도 직전에 자주 멈췄고(시간 초과 바로 뒤 USAGE_LIMIT), 판정은 첫 실패가 시간 초과여도 같은 공급자로 한 번 더 물어서 **두 번 연속 멈춘 경우가 5번**(한 번에 5분씩)이었다.
  - 수정 라운드 43번 중 16번은 테스트가 하나도 돌지 못한 상태였다. 11-13 이후 6번은 어디야 `scripts/logic-tests.sh`(로직을 Swift 패키지로 묶어 `swift test`)가 컴파일에서 실패한 경우인데, `build_errors`가 빌드 단계 출력만 봐서 컴파일 수정 경로를 타지 못하고 리뷰와 HEAD 판정을 한 바퀴씩 더 돌았다.
- `Verifier.verify`: 빌드는 통과(또는 빌드 명령 없음)했는데 테스트가 실패했고 **테스트가 0개**면 테스트 출력에서 컴파일러 줄을 뽑아 `build_errors`에 넣고 `build_errors_source = "test"`로 표시한다. 파일 위치가 붙은 오류가 하나도 없으면(예: `error: no simulator`) 넣지 않는다. 테스트가 돌고 일부가 실패한 경우는 그대로 리뷰·HEAD가 본다.
- `Orchestrator._compile_fix_rounds`: `build_errors_source == "test"`도 컴파일 수정으로 처리한다(리뷰·HEAD 없이 빌더 직행, `compile_fix_rounds` 상한·변화 없으면 중단은 그대로). 지시문에 "테스트 명령의 오류이고, 경로는 테스트 스크립트가 만든 사본(캐시 폴더, `App/Foo.swift` → `Sources/<모듈>/App_Foo.swift`)일 수 있으니 프로젝트 파일로 되짚어라"를 덧붙인다.
- `Orchestrator._run_decision_autonomous`·`MissionRunner._plan`: 첫 시도가 **시간 초과**면 같은 HEAD에 다시 묻지 않고 바로 다른 공급자로 넘긴다(빌더는 11-08부터 이렇게 했다). 넘길 공급자가 없으면 예전처럼 같은 HEAD에 한 번 더 묻는다. 다른 오류(JSON 깨짐 등)는 예전처럼 같은 공급자로 한 번 더 시도한다.
- 테스트: `tests/test_fork_repair_speed.py`에 5개(테스트 단계 컴파일), `tests/test_fork_head_timeout.py` 4개(판정 3·미션 계획 1). 새 동작 테스트는 예전 코드에서 실패하는 것을 확인했다.

## 미션 진행을 디스코드로 알리기 (2026-11-17)
- **계기**: 콘솔을 계속 보고 있지 않으면 미션이 끝났는지, 실패했는지, 한도로 멈췄는지 알 수 없었다. 기존 디스코드 알림(`core/alerts.py`, 2026-11-07)은 AI 호출 실패와 단계 실패·확인 필요만 보냈다.
- `alerts.mission_event`: 미션 진행 메시지 하나를 `제목 · 프로젝트 폴더 이름` + 본문 + `경과 시간 · 미션 ID`로 보낸다. 같은 단계를 두 번 알리지 않도록(이어서 진행 등) 미션·종류·단계별로 한 번만 보낸다.
- `MissionRunner`가 보내는 시점:
  - `mission_start` ▶️ 첫 단계 실행 시작(목표, 첫 단계, 단계 예산, 자동 적용 설정)
  - `step` ✅ 통과 / ❌ 실패 / ⚠️ 확인 필요 (단계 목표, 테스트·리뷰 한 줄 결과)
  - `mission_end` 🎉 완료 / 🙋 보류(사용자 결정 필요) / ⏸️ 일시정지(AI 사용 한도, 결정할 것 없음) / ⏹️ 중단 / ❌ 실패 (목표, 통과 단계 수, 요약, 사유, 작업본). 맥 알림 센터 배너도 띄운다.
  - `apply` 📦 자동 적용 완료 / ⚠️ 자동 적용 안 함
  - `stopped` 🛑 작업 정지
- 미션에 속한 run(`runs/<run>/mission_base.json`)의 단계 실패는 미션 메시지가 대신하므로 디스코드에 두 번 보내지 않는다(맥 배너는 그대로).
- 설정(로컬 `config.yaml`, 커밋하지 않음): `alerts.discord_webhook`에 디스코드 웹후크 주소(또는 `DIRECTOR_DISCORD_WEBHOOK` 환경 변수). 보낼 종류를 줄이려면 `alerts.discord_events: [mission_end, apply]`처럼 적는다(없으면 전부).
- 테스트: `tests/test_fork_discord_mission_alerts.py` 6개.

## 2026-11-17 · Builder can delete files; same blocking finding stops the repair loop (`feat/builder-deletes`)

Problem: in the ClipGuard M1 mission the Builder had created a root `Package.swift`, the reviewer required deleting it
(P1), and HEAD put it in `allowed_deletions` — but the proposal Builder's reply format had no delete operation and the
prompt said "No deletion operations". The same ORDER went round three mission steps (each with up to three correction
rounds) until the mission was BLOCKED, spending tokens for nothing.

Changes:
- `agents/proposal_builder.py` — replies may carry `"deletes": ["path"]` (full and repair prompts both say so).
  `resolve_deletes` accepts a path only when it is listed verbatim in `ORDER.allowed_deletions` (symbol entries such as
  `def:foo` never authorize a file), is an existing regular file inside the worktree, is not protected / control
  plane, and is not also written in the same reply; anything else rejects the whole reply before any write.
  Deletions count toward `max_files_changed`, appear in `files_modified`, and are recorded as
  `last_execution_info.deleted_files`. The post-build scope guard (`SafetyPolicy`, `allowed_deletions`) is unchanged.
- `core/orchestrator.py` — `repeated_blocking_findings`: when a correction round ends with P0/P1 findings that all
  repeat the previous round's (same text, or reworded with similarity >= 0.6), the run stops with
  `REPAIR_REPEATED_FINDINGS` instead of spending the remaining repair budget; the mission planner re-plans.
- `core/failure_reason.py` — Korean text for the two new reasons.

Tests: `tests/test_fork_builder_deletes.py` (15). `test_v306…::test_unresolved_review_after_budget_is_fail_not_human`
now expects 2 builder calls (the identical P1 stops after one correction round).

## 2026-11-17 · Readable commit / devlog titles after 「원본에 적용」 (`fix/commit-title`)

Problem: the commit title was the mission goal's first line, i.e. the raw Discord instruction cut at 72 characters
("ClipGuard M2b-1 영상 분석 만들어줘. docs/director/M2b1-analysis.md 명세대로, 결정은 do…"); the devlog draft reused it.

Changes:
- `core/apply_pipeline.py` — `title_from_goal`: first sentence of the goal without the Korean request ending
  (만들어줘·구현해줘·고쳐 주세요·추가해줘…); verbs that carry meaning become a noun (고쳐/수정해 → 수정, 추가해/넣어 → 추가,
  정리해 → 정리, 작성해 → 작성, 바꿔 → 변경). A plain-statement goal stays as it is; a result shorter than 4
  characters falls back to the whole line. When the title differs from the goal, the body keeps the request as a
  `요청: …` line (400 characters). The devlog step keeps using the commit title.

Tests: `tests/test_fork_commit_title.py` (10).

## 2026-11-17 · A P2-only PASS run can be applied (`fix/apply-p2-only`)

Problem: the HEAD-proxy final gate (2026-11-08, `test_fork_p2_gate`) passes a run with HEAD PASS, green verification
and only P2 findings even when the reviewer verdict is `changes_required`, but the apply preconditions
(`ResultManager.preconditions` / `tree_preconditions`) still required `reviewer_verdict == "pass"`. ClipGuard M2b-2
reached PASS with one P2 and 「원본에 적용」 stopped with "final_evidence is missing or not fully PASS".

Change: `ResultManager.reviewer_accepts` — `pass`, or review.json present with no P0/P1 and `head_decision == PASS`.
Every other evidence condition is unchanged. Tests: `tests/test_fork_apply_p2_only.py`.

## 2026-11-17 · Allowed deletions reach the project on 「원본에 적용」 (`fix/apply-deletions`)

Problem: the Builder could delete files since `feat/builder-deletes`, but the whole-worktree apply (`apply_tree` /
`tree_manifest`) only reported deletions ("작업본에서 지운 파일은 적용하지 않음") and the pipeline commit skipped paths
that no longer exist. ClipGuard M2b-2's allowed deletion of `ReviewPlaceholderView.swift` stayed in the project.

Changes:
- `core/result_manager.py` `tree_manifest` — a deleted path listed verbatim in the run's `order.json`
  `allowed_deletions` becomes a `deletion` manifest entry (the apply loop, backup and rollback already handled those)
  when the project file is still identical to the work's origin blob; if the user changed it meanwhile it is a
  conflict ("deletion skipped: …"). Unlisted deletions are still only reported.
- `core/apply_pipeline.py` commit — a path the apply removed is staged when git tracks it.

Tests: `tests/test_fork_apply_deletions.py` (apply + commit, rollback restores the file, unlisted only reported,
user-changed file is a conflict).

## 2026-11-17 · "예산 소진" only when the step budget ran out (`fix/incomplete-label`)

Problem: INCOMPLETE covers two endings — the step budget ran out, or HEAD could not plan the next step (e.g. every
plan rejected as OFF_GOAL). The console showed "예산 소진" for both; ClipGuard M2b-3 stopped after 1 of 6 steps.

Changes: `console/stage.js` (`missionBadge`, blocked_reason passed into the stage mission), `console/console.js`
→ "계획 실패" when `blocked_reason` starts with "HEAD could not plan"; `core/mission.py` end notification / Discord
title says "다음 단계 계획 실패" or "단계 예산 소진". Test: `test_fork_discord_mission_alerts.py` (planning failure).

## 2026-11-20 · Automatic flow: direct submit, limit guard, pre-apply review (`feat/auto-flow`)

Goal: fewer manual hops between "instruction written in Claude Code" and "user tests on a device". The user keeps three
checkpoints — confirming the instruction, pressing 「원본에 적용」, and the device test. Everything in between runs alone.

1. **Direct submit from Claude Code** — `tools/director-submit.py` (install as `~/.local/bin/director-submit`) posts
   to the existing `/bridge/missions` endpoint with the local `data/solomon-bridge.token`, `source: "claude-code"`
   (label "Claude Code 지시"), and a stable `request_id` (`cc-<date>-<sha(project+goal)>`, so a retry never starts a
   second mission). Project aliases come from `~/.config/director-intake/projects.json`. The Discord path
   (OpenClaw → `director-intake`) is unchanged and still needs a human [시작]. Skill: `~/.claude/skills/director-send`.
2. **Limit guard** — `core/limit_guard.py`, config `limit_guard` (off by default). Before a step runs and before HEAD
   plans the next step, the subscription windows of the providers in the current roles are checked
   (`subscription_limits`; a stale Claude reading is refreshed once with the small Haiku probe). At or above
   `five_hour_percent` / `weekly_percent` (or a rejected window) the mission stops as `BLOCKED` with
   `USAGE_LIMIT_GUARD: …` and a `limit_wait` record (reset time + 2 min, or `retry_minutes`). A real limit hit
   (`USAGE_LIMIT`, 2026-11-13) now gets the same record. `companion_server.limit_resume_loop` (a thread in the
   console server, not a new process) calls `dashboard_server.resume_limit_paused_missions` every
   `check_interval_sec`: once the time passed and the limits are really below the thresholds it resumes with the new
   `decide_mission(..., "resume")` (like extend, but the step budget is unchanged) and sends "▶️ 한도가 풀려 미션을
   이어서 진행해요". Still over → only the next check time moves, no repeated alert. `reopen` moves `limit_wait` into
   `limit_history`. A limit pause skips the review and auto-apply.
3. **Pre-apply review** — `core/pre_apply_review.py`, per project `pre_apply_review` (checks + `claude`). When a
   mission ends DONE: commands run in the deliverable worktree (`{worktree} {project} {report_dir} {director}`),
   files a command rewrote are restored and reported (e.g. Xcode rewriting the String Catalog), new files are
   reported; then read-only Claude (`Read,Grep,Glob`, `--add-dir report_dir`, default model sonnet) reviews the
   goal, the diff against the project (`tree_manifest` + unified diff, 60k chars), the check results and the
   screenshots, and answers `ok|warn|fail` + concerns + things to test by hand. Skipped when the limit guard says the
   limit is near. Report: `runs/<run>/review/review.md|json`; summary in mission JSON `review` and `mission_view`;
   Discord "🔎 검수 시작" / "🟢🟡🔴 검수 보고서 · …" with the console link (first `remote_access.hosts`). A `fail`
   review blocks `auto_apply`. `tools/macos/sim-screenshot.sh` installs the built simulator app and captures light and
   dark screenshots.
4. Discord events `review` and `limit` added to `alerts.MISSION_EVENTS` (sent by default).

Tests: `tests/test_fork_limit_guard.py` (7), `tests/test_fork_pre_apply_review.py` (7),
`tests/test_fork_director_submit.py` (4).

## 2026-11-20 · Second opinion from Codex / Antigravity (`tools/second-opinion.py`)

Goal: ask the other subscriptions a short read-only question from an interactive Claude Code session (design choice,
bug hypothesis, review of an uncommitted diff) without starting a Director mission. Claude, the conductor, is never
called as a worker.

- `tools/second-opinion.py` (`check` / `ask codex|agy|both [--project] [--file] [--with-diff] [--timeout] [--force]`)
  reuses `core/research_runner.TriadResearchRunner` instead of a new wrapper: subscription login only (API keys
  stripped), argv without a shell, process-tree timeout, one bounded retry for transient failures only, and the
  git-status before/after check (`RESEARCH_READ_ONLY_VIOLATION` → the answer is discarded). Each worker runs in a
  throw-away workspace: a detached worktree of the project HEAD (`create_research_workspaces`, removed afterwards)
  or an empty git repo. The original project is never touched.
- `--with-diff` appends `git diff HEAD` (40k chars max) with secret-like paths excluded (`.env*`, keys, tokens,
  `CLAUDE.md`, `config.yaml`, `GoogleService-Info.plist`).
- Workers over the `limit_guard` thresholds (read from `subscription_limits`, independent of `limit_guard.enabled`)
  are skipped unless `--force`.
- Output: JSON on stdout, `runs/second-opinion/SO-<time>-<hash>/report.md|result.json`. Calls go through
  `ProcessTreeRunner`, so they appear in the work ledger.
- Runs under the Director `.venv` automatically (re-exec when `psutil` is missing). Install:
  `ln -sf ~/work/ai-dev-director/tools/second-opinion.py ~/.local/bin/second-opinion` and copy
  `tools/skills/second-opinion/SKILL.md` to `~/.claude/skills/second-opinion/`.

Tests: `tests/test_fork_second_opinion.py` (7, fake CLIs: blank workspace, diff attached without secrets and project
untouched, diff not sent by default, write rejected, limit skip/block/force, input validation, check).
