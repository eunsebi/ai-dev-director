#!/usr/bin/python3
"""director-submit: 맥 Claude Code → Director 바로 접수·시작 (fork 2026-11-20).

예전: Claude Code가 지시문 작성 → 디스코드에 붙여 넣기 → OpenClaw 접수 → Director 콘솔에서 [시작].
이제: Claude Code가 이 명령으로 맥 안의 콘솔 서버(127.0.0.1:8081)에 바로 보내고, 미션이 곧바로 시작된다.
사람이 앉아 있는 대화형 세션에서만 쓴다(Director 안의 비대화형 실행·디스코드 접수 경로와는 별개).
디스코드 접수(OpenClaw → director-intake)는 그대로 「시작은 사람이」를 유지한다.

  director-submit projects                       접수할 수 있는 프로젝트(별칭 → 경로)
  director-submit start <별칭|경로> [--file 지시문.md] [--profile quick|standard|deep]
                                                 지시문은 --file 또는 표준입력. 같은 지시문을 다시 보내면 새로 만들지 않음
  director-submit status <MSN-…>                 미션 상태(단계·한도 일시정지·검수 보고서)
  director-submit list                           최근 미션 10개

인증: Director가 만든 data/solomon-bridge.token(로컬 파일)을 X-Solomon-Bridge 헤더로. 토큰은 출력하지 않는다.
별칭: ~/.config/director-intake/projects.json(디스코드 접수와 같은 목록).
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DIRECTOR = Path(os.environ.get("DIRECTOR_HOME") or HOME / "work" / "ai-dev-director")
TOKEN = DIRECTOR / "data" / "solomon-bridge.token"
PROJECTS = Path(os.environ.get("DIRECTOR_INTAKE_PROJECTS") or HOME / ".config" / "director-intake" / "projects.json")
BASE = os.environ.get("DIRECTOR_URL") or "http://127.0.0.1:8081"


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def projects():
    try:
        raw = json.loads(PROJECTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (raw or {}).items() if isinstance(v, dict) and v.get("path")}


def resolve_project(name):
    p = projects().get(name)
    if p:
        return str(Path(p["path"]).expanduser().resolve())
    path = Path(name).expanduser()
    if path.is_dir():
        return str(path.resolve())
    out({"ok": False, "error": f"모르는 프로젝트입니다: {name}", "projects": sorted(projects())}, 2)


def call(method, path, body=None):
    try:
        token = TOKEN.read_text(encoding="utf-8").strip()
    except OSError:
        out({"ok": False, "error": "Director 토큰 파일이 없습니다. 콘솔 서버를 한 번 켜 주세요(메뉴 앱)."}, 3)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Solomon-Bridge": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        out({"ok": False, "error": f"Director 콘솔 서버(8081)에 연결하지 못했습니다: {e}. 메뉴 앱에서 켜 주세요."}, 3)


def brief(m):
    steps = m.get("steps") or []
    last = steps[-1] if steps else {}
    return {"mission_id": m.get("mission_id"), "project": m.get("project"), "status": m.get("status"),
            "goal": str(m.get("goal") or "")[:160],
            "steps": f"{m.get('passed_steps', '?')}/{len(steps)} 통과 (예산 {m.get('max_steps')})",
            "last_step": {k: last.get(k) for k in ("goal", "state_ko", "tests") if last.get(k)},
            "blocked_reason": (m.get("blocked_reason") or "")[:300] or None,
            "limit_wait": m.get("limit_wait"), "review": m.get("review"), "apply_state": m.get("apply_state"),
            "deliverable": m.get("deliverable")}


def start(args):
    project = resolve_project(args.project)
    goal = (Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()).strip()
    if not 5 <= len(goal) <= 6000:
        out({"ok": False, "error": "지시문은 5~6000자여야 합니다", "length": len(goal)}, 2)
    digest = hashlib.sha256(f"{project}\n{goal}".encode("utf-8")).hexdigest()[:16]
    request_id = f"cc-{datetime.now():%Y%m%d}-{digest}"
    body = {"request_id": request_id, "project_path": project, "goal": goal, "source": "claude-code"}
    if args.profile:
        body["profile"] = args.profile
    code, res = call("POST", "/bridge/missions", body)
    status = res.get("status")
    say = {"started": "미션을 시작했어요. 진행 알림은 디스코드 개발-알림 채널로 와요.",
           "duplicate": "같은 지시문으로 이미 시작한 미션이 있어요(새로 만들지 않음).",
           "busy": "이 프로젝트에서 다른 미션이 돌고 있어요. 끝난 뒤 다시 보내 주세요.",
           "question": "HEAD가 지시문에 대해 질문했어요. 답을 지시문에 넣어 다시 보내 주세요.",
           "escalated": "HEAD가 사람 확인이 필요하다고 판단했어요(결제·자격 증명 등). 콘솔에서 확인해 주세요."}
    out({"ok": status in ("started", "duplicate"), "http": code, "status": status, "say": say.get(status, res.get("error")),
         "mission_id": res.get("mission_id"), "message": res.get("message"), "error": res.get("error"),
         "project_path": project, "request_id": request_id}, 0 if status in ("started", "duplicate", "question") else 1)


def main():
    ap = argparse.ArgumentParser(prog="director-submit")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("projects")
    s = sub.add_parser("start")
    s.add_argument("project")
    s.add_argument("--file")
    s.add_argument("--profile", choices=["quick", "standard", "deep"])
    st = sub.add_parser("status")
    st.add_argument("mission_id")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "projects":
        out({"ok": True, "projects": {k: v.get("path") for k, v in projects().items()}})
    if args.cmd == "start":
        start(args)
    if args.cmd == "status":
        code, res = call("GET", f"/bridge/missions/{args.mission_id}")
        out({"ok": code == 200, **(brief(res) if code == 200 else {"error": res.get("error")})}, 0 if code == 200 else 1)
    if args.cmd == "list":
        code, res = call("GET", "/bridge/missions")
        out({"ok": code == 200, "missions": [brief(m) for m in (res.get("missions") or [])[:10]]})
    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
