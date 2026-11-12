#!/usr/bin/python3
"""director-intake: OpenClaw VM → 맥 '접수 전용' SSH 명령 (2026-11-12).

맥 ~/.ssh/authorized_keys의 VM 열쇠 줄에 command="~/.local/bin/director-intake",restrict 로 묶는다.
그 열쇠로는 이 명령만 돈다(셸·포워딩 없음). 받은 요청은 Director 「디스코드 접수함」에 쌓이고,
사용자가 Director 콘솔에서 [시작]을 눌러야만 미션이 시작된다.

  ssh mac-intake projects            허용 프로젝트 목록
  ssh mac-intake submit <프로젝트>   표준입력(JSON {"request": "...", "from": "..."} 또는 글자)을 접수
  ssh mac-intake status <REQ-…>      접수 상태와 미션 진행
  ssh mac-intake list                최근 접수 10개

허용 프로젝트: ~/.config/director-intake/projects.json  {"eodiya": {"path": "/abs/path", "name": "어디야"}}
"""
import json, os, re, secrets, sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DIRECTOR = Path(os.environ.get("DIRECTOR_HOME") or HOME / "work" / "ai-dev-director")
INTAKE = Path(os.environ.get("DIRECTOR_INTAKE_DIR") or DIRECTOR / "data" / "intake")
MISSIONS = DIRECTOR / "data" / "missions"
PROJECTS = Path(os.environ.get("DIRECTOR_INTAKE_PROJECTS") or HOME / ".config" / "director-intake" / "projects.json")
ID_RE = re.compile(r"^REQ-\d{14}-[0-9a-f]{6}$")
MAX_WAITING, MAX_PER_DAY = 10, 20
SECRET = re.compile(r"Bearer [A-Za-z0-9_-]{20,}|(PASSWORD|[Pp]ass(word|wd)?|비밀번호|비번)\s*[:=]\s*\S{6,}|sk-[A-Za-z0-9_-]{20,}"
                    r"|gh[po]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|pcn_[A-Za-z0-9_-]{30,}|PRIVATE KEY-----|AKIA[0-9A-Z]{16}")
SAY = {"waiting": "시작 대기(맥 Director 콘솔에서 [시작]을 눌러야 시작)", "started": "시작함", "discarded": "버림"}


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def projects():
    try:
        raw = json.loads(PROJECTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (raw or {}).items() if re.fullmatch(r"[a-z0-9-]{1,30}", str(k)) and isinstance(v, dict) and v.get("path")}


def items():
    if not INTAKE.exists():
        return []
    res = []
    for p in sorted(INTAKE.glob("REQ-*.json"), reverse=True):
        try:
            res.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return res


def mission_info(mid):
    if not mid or not re.fullmatch(r"MSN-[0-9A-Za-z-]{6,60}", mid):
        return None
    try:
        m = json.loads((MISSIONS / f"{mid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    steps = m.get("steps") or []
    last = steps[-1] if steps else {}
    return {"mission_id": mid, "status": m.get("status"), "steps": len(steps), "last_step": {k: last.get(k) for k in ("title", "state", "verdict") if last.get(k)},
            "blocked_reason": (m.get("blocked_reason") or "")[:300] or None, "updated_at": m.get("updated_at")}


def submit(project):
    proj = projects().get(project)
    if not proj:
        out({"ok": False, "error": "허용되지 않은 프로젝트입니다", "allowed": sorted(projects())}, 2)
    raw = sys.stdin.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        out({"ok": False, "error": "요청이 너무 깁니다(16KB)"}, 2)
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        body = {"request": raw}
    text = re.sub(r"[\u0000-\u0008\u000b-\u001f\u007f]", " ", str(body.get("request") or "")).strip()
    sender = re.sub(r"[^\w .@#:-]", "", str(body.get("from") or ""))[:60]
    if not 5 <= len(text) <= 4000:
        out({"ok": False, "error": "요청은 5~4000자여야 합니다"}, 2)
    if SECRET.search(text):
        out({"ok": False, "error": "비밀값(토큰·비밀번호 등)으로 보이는 내용이 있어 받지 않았습니다"}, 2)
    all_items = items()
    # 같은 요청을 한 번 더 보내면(재시도 등) 새로 만들지 않고 기다리는 접수 번호를 돌려준다
    same = next((i for i in all_items if i.get("status") == "waiting" and i.get("project") == project and i.get("request") == text), None)
    if same:
        out({"ok": True, "id": same["id"], "duplicate": True, "project": proj.get("name", project),
             "message": f"이미 접수된 요청입니다({same['id']}). 맥 Director 작업실 「디스코드 접수함」에서 [시작]을 누르면 됩니다."})
    today = datetime.now().strftime("%Y%m%d")
    if sum(1 for i in all_items if i.get("status") == "waiting") >= MAX_WAITING:
        out({"ok": False, "error": f"시작을 기다리는 접수가 {MAX_WAITING}개입니다. Director에서 먼저 처리하세요"}, 3)
    if sum(1 for i in all_items if str(i.get("id", "")).startswith(f"REQ-{today}")) >= MAX_PER_DAY:
        out({"ok": False, "error": f"오늘은 {MAX_PER_DAY}개까지 접수할 수 있습니다"}, 3)
    ident = f"REQ-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    item = {"id": ident, "project": project, "project_path": str(Path(proj["path"]).resolve()), "request": text, "from": sender or None,
            "received_at": datetime.now().isoformat(timespec="seconds"), "status": "waiting", "via": "openclaw-vm"}
    INTAKE.mkdir(parents=True, exist_ok=True)
    tmp = INTAKE / f"{ident}.tmp"
    tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, INTAKE / f"{ident}.json")
    out({"ok": True, "id": ident, "project": proj.get("name", project),
         "message": f"접수했습니다({ident}). 맥 Director 작업실 탭 「디스코드 접수함」(위쪽 「디스코드 접수 N」 버튼)에서 [시작]을 누르면 미션이 시작됩니다."})


def status(ident):
    if not ID_RE.fullmatch(ident or ""):
        out({"ok": False, "error": "접수 번호 형식이 아닙니다(REQ-…)"}, 2)
    try:
        it = json.loads((INTAKE / f"{ident}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out({"ok": False, "error": "없는 접수 번호입니다"}, 2)
    out({"ok": True, "id": ident, "project": it.get("project"), "status": it.get("status"), "say": SAY.get(it.get("status"), it.get("status")),
         "received_at": it.get("received_at"), "started_at": it.get("started_at"), "last_result": it.get("last_result"),
         "mission": mission_info(it.get("mission_id"))})


def main():
    args = (os.environ.get("SSH_ORIGINAL_COMMAND") or " ".join(sys.argv[1:])).split()
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    if cmd == "projects" and not rest:
        out({"ok": True, "projects": {k: v.get("name", k) for k, v in projects().items()}})
    if cmd == "submit" and len(rest) == 1:
        submit(rest[0])
    if cmd == "status" and len(rest) == 1:
        status(rest[0])
    if cmd == "list" and not rest:
        out({"ok": True, "items": [{"id": i.get("id"), "project": i.get("project"), "status": i.get("status"), "request": str(i.get("request", ""))[:80],
                                     "mission_id": i.get("mission_id")} for i in items()[:10]]})
    out({"ok": False, "error": "할 수 있는 명령: projects | submit <프로젝트> | status <REQ-…> | list"}, 2)


if __name__ == "__main__":
    main()
