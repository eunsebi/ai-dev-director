"""한도 예상 일시정지와 자동 재개 (fork 2026-11-20).

Director 미션·대화형 Claude Code·검수 보고서가 같은 구독 한도(Claude 5시간·주간, Codex, Antigravity)를 나눠 쓴다.
한도를 다 쓴 다음에 멈추면(USAGE_LIMIT) 그 단계가 망가지고 다시 돌아야 하므로, 단계를 시작하기 전에 남은 한도를 보고
문턱을 넘었으면 미리 멈춘다(BLOCKED "USAGE_LIMIT_GUARD: …"). 미션 JSON의 `limit_wait`에 풀리는 시각을 적어 두면,
콘솔 서버 안의 감시 스레드(`resume_due`, 새 프로그램 아님)가 그 시각 뒤에 한도를 다시 보고 미션을 이어서 진행한다.
실제로 한도에 걸려 멈춘 미션(USAGE_LIMIT, 2026-11-13)도 같은 방식으로 자동 재개한다.

config.yaml:
    limit_guard:
      enabled: true              # 기본 꺼짐
      five_hour_percent: 90      # 5시간 창 사용률이 이 값 이상이면 멈춤
      weekly_percent: 95         # 주간·월 창
      probe_max_age_min: 10      # Claude 한도 정보가 이보다 오래됐으면 단계 시작 전에 한 번 새로 확인(Haiku 짧은 호출)
      auto_resume: true          # 풀리는 시각이 지나면 자동으로 이어서 진행
      retry_minutes: 30          # 풀리는 시각을 모를 때 다시 볼 간격
      check_interval_sec: 300    # 감시 스레드 주기
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

KST = timezone(timedelta(hours=9))
GUARD_PREFIX = "USAGE_LIMIT_GUARD"
LIMIT_PREFIX = "USAGE_LIMIT"
PROVIDER_NAMES = {"ANTHROPIC": "Claude", "OPENAI": "Codex", "GOOGLE": "Antigravity"}
DEFAULTS = {"enabled": False, "five_hour_percent": 90, "weekly_percent": 95, "probe_max_age_min": 10,
            "auto_resume": True, "retry_minutes": 30, "check_interval_sec": 300}


def settings(config: Optional[dict]) -> Dict[str, Any]:
    raw = (config or {}).get("limit_guard") or {}
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in DEFAULTS})
    out["enabled"] = bool(out["enabled"]) and out["enabled"] not in ("off", "false")
    return out


def providers_in_use(config: Optional[dict]) -> List[str]:
    """HEAD·BUILDER·REVIEWER가 쓰는 구독(ANTHROPIC/OPENAI/GOOGLE), 겹치면 한 번만."""
    try:
        from core.role_settings import current, DOMAINS
        roles = current(config or {})
        found = [DOMAINS[v] for v in (roles.get("head"), roles.get("builder"), roles.get("reviewer")) if v in DOMAINS]
    except Exception:
        found = ["ANTHROPIC"]
    return list(dict.fromkeys(found)) or ["ANTHROPIC"]


def _threshold(window: Dict[str, Any], opts: Dict[str, Any]) -> float:
    key, label = str(window.get("key") or ""), str(window.get("label") or "")
    minutes = window.get("window_minutes") or 0
    if key == "five_hour" or label == "5시간" or minutes == 300:
        return float(opts["five_hour_percent"])
    return float(opts["weekly_percent"])


def over_limit(snapshot: Dict[str, Any], providers: List[str], opts: Dict[str, Any],
               now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """문턱을 넘은 창 하나({provider, label, used_percent, resets_at, rejected}) 또는 None.
    풀리는 시각이 이미 지난 창은 새 창이 시작된 것이므로 0%로 본다. 여럿이면 가장 늦게 풀리는 것."""
    now = now or time.time()
    hits = []
    for prov in providers:
        entry = ((snapshot or {}).get("providers") or {}).get(prov) or {}
        for w in entry.get("windows") or []:
            resets = w.get("resets_at")
            try:
                resets = float(resets) if resets is not None else None
            except (TypeError, ValueError):
                resets = None
            if resets and resets <= now:
                continue
            used = w.get("used_percent")
            rejected = str(w.get("status") or "").lower() == "rejected"
            if rejected or (isinstance(used, (int, float)) and used >= _threshold(w, opts)):
                hits.append({"provider": prov, "label": w.get("label") or w.get("key"), "used_percent": used,
                             "resets_at": resets, "rejected": rejected})
    if not hits:
        return None
    hits.sort(key=lambda h: h["resets_at"] or 0)
    return hits[-1]


def fresh_snapshot(config: Optional[dict], opts: Dict[str, Any], providers: List[str]) -> Dict[str, Any]:
    """구독 한도 묶음. Claude를 쓰는데 정보가 오래됐으면 여기서 한 번(동기) 새로 확인한다."""
    from core import subscription_limits as sl
    if "ANTHROPIC" in providers:
        entry = sl._read_cache().get("claude") or {}
        age_min = (time.time() - float(entry.get("checked_at") or 0)) / 60
        if age_min >= float(opts["probe_max_age_min"]):
            with sl._lock:
                start = not sl._probing
                sl._probing = sl._probing or start
            if start:
                sl._probe_claude()                  # 짧은 Haiku 호출(작업 장부에는 안 적힘)
    return {"providers": {"ANTHROPIC": dict(sl._read_cache().get("claude") or {}),
                          "OPENAI": sl.codex_limits() if "OPENAI" in providers else {},
                          "GOOGLE": sl.antigravity_limits(0) if "GOOGLE" in providers else {}}}


def check(config: Optional[dict], snapshot_fn: Optional[Callable[..., Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """단계·계획을 시작하기 전에 부른다. 멈춰야 하면 {provider, label, used_percent, resets_at, reason}."""
    opts = settings(config)
    if not opts["enabled"]:
        return None
    providers = providers_in_use(config)
    try:
        snap = (snapshot_fn or fresh_snapshot)(config, opts, providers)
    except Exception:
        return None                                 # 한도를 못 읽으면 막지 않는다(실제 한도는 USAGE_LIMIT이 잡는다)
    hit = over_limit(snap, providers, opts)
    if hit:
        hit["reason"] = describe(hit)
    return hit


def when(ts: Optional[float]) -> str:
    if not ts:
        return "알 수 없음"
    return datetime.fromtimestamp(float(ts), KST).strftime("%m-%d %H:%M")


def describe(hit: Dict[str, Any]) -> str:
    name = PROVIDER_NAMES.get(hit.get("provider"), hit.get("provider"))
    used = hit.get("used_percent")
    used_txt = "한도 도달" if hit.get("rejected") or used is None else f"{used:g}% 사용"
    return f"{name} {hit.get('label')} {used_txt} · 풀리는 시각 {when(hit.get('resets_at'))}"


def wait_record(hit: Optional[Dict[str, Any]], opts: Dict[str, Any], kind: str, now: Optional[float] = None) -> Dict[str, Any]:
    """미션 JSON `limit_wait`: 언제 다시 볼지. 풀리는 시각 + 2분, 모르면 retry_minutes 뒤."""
    now = now or time.time()
    resets = (hit or {}).get("resets_at")
    resume_after = float(resets) + 120 if resets and resets > now else now + float(opts["retry_minutes"]) * 60
    return {"kind": kind, "provider": (hit or {}).get("provider"), "label": (hit or {}).get("label"),
            "used_percent": (hit or {}).get("used_percent"), "resets_at": resets, "resume_after": resume_after,
            "auto_resume": bool(opts["auto_resume"]), "since": now}


def is_limit_pause(mission: Dict[str, Any]) -> bool:
    return mission.get("status") == "BLOCKED" and str(mission.get("blocked_reason") or "").startswith(LIMIT_PREFIX)


def resume_due(config: Optional[dict], store, resume: Callable[[str], dict], alert: Callable[..., Any],
               snapshot_fn: Optional[Callable[..., Dict[str, Any]]] = None, now: Optional[float] = None) -> List[str]:
    """감시 스레드가 주기적으로 부른다. 한도로 멈춘 미션 중 다시 볼 시각이 지난 것을 확인하고, 한도가 풀렸으면 재개한다.
    아직이면 다음 시각만 미룬다(알림은 처음 멈출 때 한 번만). 재개한 mission_id 목록을 돌려준다."""
    opts = settings(config)
    if not opts["enabled"] or not opts["auto_resume"]:
        return []
    now = now or time.time()
    resumed = []
    for m in store.all():
        if not is_limit_pause(m):
            continue
        wait = m.get("limit_wait") or {}
        if wait.get("auto_resume") is False:
            continue
        if not wait:                                  # 예전 USAGE_LIMIT 정지(기록 없음): 지금부터 기다린 것으로 시작
            m["limit_wait"] = wait_record(None, opts, "hit", now)
            m["limit_wait"]["resume_after"] = now
            wait = m["limit_wait"]
        if float(wait.get("resume_after") or 0) > now:
            continue
        providers = providers_in_use(config)
        try:
            snap = (snapshot_fn or fresh_snapshot)(config, opts, providers)
            hit = over_limit(snap, providers, opts, now)
        except Exception:
            hit = {"provider": None, "label": None, "resets_at": None}
        if hit:
            nxt = wait_record(hit, opts, wait.get("kind") or "guard", now)
            m["limit_wait"] = {**wait, "resume_after": nxt["resume_after"], "resets_at": nxt["resets_at"] or wait.get("resets_at"),
                               "checks": int(wait.get("checks") or 0) + 1}
            store.save(m)
            continue
        store.save(dict(m, limit_wait={**wait, "resumed_at": now}))
        result = resume(m["mission_id"]) or {}
        if result.get("status") == "resumed":
            resumed.append(m["mission_id"])
            alert("limit", m, "▶️ 한도가 풀려 미션을 이어서 진행해요",
                  [f"목표: {str(m.get('goal') or '')[:300]}", f"멈춘 이유: {str(m.get('blocked_reason') or '')[:300]}"],
                  dedupe=f"resume:{int(now)}")
    return resumed
