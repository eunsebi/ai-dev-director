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
