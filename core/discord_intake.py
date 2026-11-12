"""디스코드 접수함 (2026-11-12).

OpenClaw VM이 맥의 '접수 전용' SSH 열쇠(authorized_keys의 forced command ~/.local/bin/director-intake)로 남긴 개발 요청을
data/intake/REQ-*.json에 쌓는다. 여기서는 읽기만 하고, 사용자가 콘솔에서 [시작]을 누를 때만 start_goal_mission으로 넘긴다.
OpenClaw가 웹 글에 속아 요청을 남겨도 사람이 시작하기 전에는 아무 코드도 돌지 않는다. 원본 적용은 지금처럼 「원본에 적용」으로만.

허용 프로젝트: ~/.config/director-intake/projects.json  {"eodiya": {"path": "/abs/path", "name": "어디야"}}
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
ID_RE = re.compile(r"^REQ-\d{14}-[0-9a-f]{6}$")
STARTED = {"started", "question", "escalated"}


def intake_dir() -> Path:
    return Path(os.environ.get("DIRECTOR_INTAKE_DIR") or (ROOT / "data" / "intake"))


def projects_file() -> Path:
    return Path(os.environ.get("DIRECTOR_INTAKE_PROJECTS") or (Path.home() / ".config" / "director-intake" / "projects.json"))


def projects() -> Dict[str, Dict[str, str]]:
    try:
        raw = json.loads(projects_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for key, v in (raw or {}).items():
        if re.fullmatch(r"[a-z0-9-]{1,30}", str(key)) and isinstance(v, dict) and v.get("path"):
            out[key] = {"path": str(v["path"]), "name": str(v.get("name") or key)}
    return out


def _path(ident: str) -> Path:
    if not ID_RE.fullmatch(ident or ""):
        raise ValueError("알 수 없는 접수 번호입니다")
    return intake_dir() / f"{ident}.json"


def load(ident: str) -> Dict[str, Any]:
    p = _path(ident)
    if not p.exists():
        raise ValueError("알 수 없는 접수 번호입니다")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(item: Dict[str, Any]) -> None:
    p = _path(item["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def list_items(limit: int = 30) -> List[Dict[str, Any]]:
    d = intake_dir()
    if not d.exists():
        return []
    items = []
    for p in sorted(d.glob("REQ-*.json"), reverse=True)[:limit]:
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return items


def start(ident: str, start_goal_mission: Callable[..., dict]) -> dict:
    """사람이 [시작]을 눌렀을 때만. 허용 목록을 다시 확인하고 미션을 시작한다."""
    item = load(ident)
    if item.get("status") != "waiting":
        raise ValueError("이미 처리한 접수입니다")
    proj = projects().get(item.get("project", ""))
    if not proj or Path(proj["path"]).resolve() != Path(item.get("project_path", "")).resolve():
        raise ValueError("허용 목록에 없는 프로젝트입니다")
    goal = str(item.get("request", "")).strip()
    result = start_goal_mission(proj["path"], goal, source="discord", request_id=ident)
    item["last_result"] = {k: result.get(k) for k in ("status", "error", "message", "mission_id", "run_id") if result.get(k)}
    if result.get("status") in STARTED:
        item["status"] = "started"
        item["started_at"] = datetime.now().isoformat(timespec="seconds")
        item["mission_id"] = result.get("mission_id")
    _save(item)
    return result


def discard(ident: str, reason: str = "") -> dict:
    item = load(ident)
    if item.get("status") != "waiting":
        raise ValueError("이미 처리한 접수입니다")
    item.update(status="discarded", discarded_at=datetime.now().isoformat(timespec="seconds"), discard_reason=reason[:200])
    _save(item)
    return {"status": "discarded", "id": ident}
