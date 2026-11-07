"""Failure alerts outside the browser (fork 2026-11-07).

macOS: a Notification Center banner through `osascript` (no extra install), so a failure reaches the user even when
the dashboard tab is closed. Throttled per (source, reason) so a retry loop does not flood the screen. Never raises,
never blocks the caller. Off under unit tests (DIRECTOR_TESTING=1) and with DIRECTOR_MAC_ALERTS=0.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "data" / "alert_state.json"
THROTTLE_SEC = 600
SKIP_CODES = {"INTERRUPTED"}          # the user stopped it; no news
_LOCK = threading.Lock()


def enabled() -> bool:
    return (sys.platform == "darwin" and os.environ.get("DIRECTOR_TESTING") != "1"
            and os.environ.get("DIRECTOR_MAC_ALERTS", "1") != "0")


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


def notify(title: str, message: str, key: str) -> bool:
    if not enabled() or not _due(key):
        return False
    script = f"display notification {_quote(message)} with title {_quote(title)} sound name \"Basso\""
    try:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def call_failed(provider_name: str, role: str, why: dict) -> None:
    try:
        if why.get("code") in SKIP_CODES:
            return
        notify(f"AI DEV DIRECTOR · {provider_name} {role or ''} 실패".strip(),
               f"{why.get('label', '')} — {why.get('hint', '')}", f"call:{provider_name}:{why.get('code')}")
    except Exception:
        pass


def step_failed(run_id: str, to_state: str, details: str) -> None:
    try:
        from core.failure_reason import classify_step
        why = classify_step(to_state, details or "")
        notify(f"AI DEV DIRECTOR · 작업 단계 {'확인 필요' if to_state == 'HUMAN_REQUIRED' else '실패'}",
               f"{why['label']} — {why['hint']}", f"step:{why['code']}:{why.get('cause')}")
    except Exception:
        pass
