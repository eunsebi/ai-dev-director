"""Failure alerts outside the browser (fork 2026-11-07).

- macOS: a Notification Center banner through `osascript` (no extra install), so a failure reaches the user even
  when the dashboard tab is closed. Off with DIRECTOR_MAC_ALERTS=0.
- Discord: a message to a channel webhook, so it reaches the phone. The webhook URL is a secret: it is read from
  config.yaml `alerts.discord_webhook` (never committed) or the DIRECTOR_DISCORD_WEBHOOK environment variable.
  Only https://discord.com/api/webhooks/... (or discordapp.com) URLs are used.
Throttled per (source, reason) so a retry loop does not flood. Never raises, never blocks the caller. Everything is
off under unit tests (DIRECTOR_TESTING=1).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "data" / "alert_state.json"
THROTTLE_SEC = 600
SKIP_CODES = {"INTERRUPTED"}          # the user stopped it; no news
_LOCK = threading.Lock()


CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
_WEBHOOK = re.compile(r"^https://(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+$")


def _testing() -> bool:
    return os.environ.get("DIRECTOR_TESTING") == "1"


def enabled() -> bool:
    return sys.platform == "darwin" and not _testing() and os.environ.get("DIRECTOR_MAC_ALERTS", "1") != "0"


def discord_webhook() -> str:
    url = os.environ.get("DIRECTOR_DISCORD_WEBHOOK", "").strip()
    if not url:
        try:
            import yaml
            with open(CONFIG, encoding="utf-8") as f:
                url = str(((yaml.safe_load(f) or {}).get("alerts") or {}).get("discord_webhook") or "").strip()
        except Exception:
            url = ""
    return url if _WEBHOOK.match(url) else ""


def _post_discord(url: str, title: str, message: str) -> None:
    import urllib.request
    body = json.dumps({"username": "AI DEV DIRECTOR", "content": f"**{title}**\n{message}"[:1900],
                       "allowed_mentions": {"parse": []}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "ai-dev-director-alerts"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass


def _quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', "'").replace("\n", " ")[:220] + '"'


def _due(key: str) -> bool:
    with _LOCK:
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        now = time.time()
        if now - float(state.get(key, 0)) < THROTTLE_SEC:
            return False
        state = {k: v for k, v in state.items() if now - float(v) < 86400}
        state[key] = now
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass
        return True


def notify(title: str, message: str, key: str, discord: bool = True) -> bool:
    if _testing():
        return False
    mac, hook = enabled(), (discord_webhook() if discord else "")
    if not (mac or hook) or not _due(key):
        return False
    sent = False
    if mac:
        script = f"display notification {_quote(message)} with title {_quote(title)} sound name \"Basso\""
        try:
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sent = True
        except OSError:
            pass
    if hook:
        threading.Thread(target=_post_discord, args=(hook, title, message), daemon=True).start()
        sent = True
    return sent


def call_failed(provider_name: str, role: str, why: dict) -> None:
    try:
        if why.get("code") in SKIP_CODES:
            return
        notify(f"AI DEV DIRECTOR · {provider_name} {role or ''} 실패".strip(),
               f"{why.get('label', '')} — {why.get('hint', '')}", f"call:{provider_name}:{why.get('code')}")
    except Exception:
        pass


def _in_mission(run_id: str) -> bool:
    return bool(run_id) and (Path(__file__).resolve().parent.parent / "runs" / run_id / "mission_base.json").exists()


def step_failed(run_id: str, to_state: str, details: str) -> None:
    try:
        from core.failure_reason import classify_step
        why = classify_step(to_state, details or "")
        # a mission step's outcome is announced by mission_event (with the mission, project and step), so Discord
        # gets it once; the Mac banner stays as it was
        notify(f"AI DEV DIRECTOR · 작업 단계 {'확인 필요' if to_state == 'HUMAN_REQUIRED' else '실패'}",
               f"{why['label']} — {why['hint']}", f"step:{why['code']}:{why.get('cause')}",
               discord=not _in_mission(run_id))
    except Exception:
        pass


# ------------------------------------------------------------------ mission progress (fork 2026-11-17)
# Missions run for many minutes; without the console open nobody saw that one finished, failed or paused for the
# usage limit. Each event goes to the Discord webhook (and, for the end of a mission, the Mac banner).
# config.yaml `alerts.discord_events` picks which kinds are sent; by default all of them.
# fork 2026-11-20: review = 적용 전 검수 보고서, limit = 한도가 풀려 자동으로 이어서 진행
MISSION_EVENTS = ("mission_start", "step", "mission_end", "apply", "stopped", "review", "limit")


def discord_events() -> set:
    try:
        import yaml
        with open(CONFIG, encoding="utf-8") as f:
            chosen = ((yaml.safe_load(f) or {}).get("alerts") or {}).get("discord_events")
    except Exception:
        chosen = None
    if not isinstance(chosen, list):
        return set(MISSION_EVENTS)
    return {str(c) for c in chosen} & set(MISSION_EVENTS)


def _elapsed(created_at: str) -> str:
    try:
        from datetime import datetime
        minutes = int((datetime.utcnow() - datetime.fromisoformat(str(created_at))).total_seconds() // 60)
    except Exception:
        return ""
    return f"{minutes // 60}시간 {minutes % 60}분" if minutes >= 60 else f"{max(minutes, 0)}분"


def mission_event(kind: str, mission: dict, title: str, lines, dedupe: str = "", mac: bool = False) -> bool:
    """One mission progress message: `title · project`, the given lines, then elapsed time and the mission id.
    `dedupe` keeps a resumed mission from announcing the same step twice. Never raises."""
    try:
        if _testing() or not isinstance(mission, dict):
            return False
        hook = discord_webhook() if kind in discord_events() else ""
        mac = mac and enabled()
        if not (hook or mac):
            return False
        mission_id = str(mission.get("mission_id") or "")
        if not _due(f"mission:{mission_id}:{kind}:{dedupe}"):
            return False
        project = Path(str(mission.get("project_path") or "")).name or "프로젝트"
        body = [str(x) for x in (lines or []) if x]
        tail = " · ".join(x for x in (_elapsed(mission.get("created_at")) and "경과 " + _elapsed(mission.get("created_at")),
                                      mission_id) if x)
        if hook:
            threading.Thread(target=_post_discord, args=(hook, f"{title} · {project}", "\n".join(body + [tail])),
                             daemon=True).start()
        if mac:
            script = (f"display notification {_quote(body[0] if body else title)} "
                      f"with title {_quote(title + ' · ' + project)} sound name \"Glass\"")
            try:
                subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        return True
    except Exception:
        return False
