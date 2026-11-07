"""Subscription limits per AI for the stage screen's workload panel (fork 2026-11-07).

- Claude: `claude --print --output-format stream-json --verbose` emits a `rate_limit_event` with the 5-hour and
  7-day windows (utilization 0..1, resetsAt epoch). This is what the VS Code extension shows. We run one tiny
  Haiku probe at most every `subscription_limits.claude_probe_minutes` (default 10, 0 = off), only while someone
  is looking at the dashboard, in a background thread, and cache it in data/subscription_limits.json.
  The probe is NOT written to the work ledger (it is not Director work).
- Codex: every Codex session already logs `rate_limits` (used_percent, window_minutes, resets_at) in
  ~/.codex/sessions/**/rollout-*.jsonl - read-only, no extra call.
- Antigravity: `agy -p /usage --output-format json` is a local slash command (0 tokens, no model turn) that returns
  the weekly buckets per model group (remaining_fraction, reset_time). Same cache/background pattern as Claude,
  every `subscription_limits.antigravity_probe_minutes` (default 10). When it fails we fall back to the latest
  USAGE_LIMIT failure from the ledger.
"""
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "subscription_limits.json"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
DEFAULT_PROBE_MINUTES = 10
PROBE_TIMEOUT = 90

_lock = threading.Lock()
_probing = False
_agy_probing = False


def _now() -> float:
    return time.time()


def _read_cache() -> Dict[str, Any]:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(data: Dict[str, Any]) -> None:
    try:
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
    except OSError:
        pass


# ------------------------------------------------------------------ Claude
def parse_claude_stream(stdout: str) -> Optional[Dict[str, Any]]:
    """The last rate_limit_event of a stream-json run -> {windows: [...], status}."""
    info = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{") or "rate_limit_event" not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "rate_limit_event" and isinstance(ev.get("rate_limit_info"), dict):
            info = ev["rate_limit_info"]
    if info is None:
        return None
    names = {"five_hour": "5시간", "seven_day": "주간", "seven_day_opus": "주간(Opus)", "seven_day_sonnet": "주간(Sonnet)"}
    windows = []
    for key, w in (info.get("unifiedWindows") or {}).items():
        if not isinstance(w, dict):
            continue
        util = w.get("utilization")
        windows.append({"key": key, "label": names.get(key, key),
                        "used_percent": round(float(util) * 100, 1) if isinstance(util, (int, float)) else None,
                        "resets_at": w.get("resetsAt"), "status": w.get("status")})
    if not windows and info.get("rateLimitType"):          # older CLI: only the window that is closest to the limit
        windows.append({"key": info["rateLimitType"], "label": names.get(info["rateLimitType"], info["rateLimitType"]),
                        "used_percent": None, "resets_at": info.get("resetsAt"), "status": info.get("status")})
    order = list(names)
    windows.sort(key=lambda w: order.index(w["key"]) if w["key"] in order else 99)
    return {"windows": windows, "status": info.get("status")}


def _probe_claude() -> None:
    global _probing
    try:
        from core.env_utils import resolve_cli, get_sanitized_env
        exe = resolve_cli("claude")
        entry: Dict[str, Any] = {"checked_at": _now()}
        if not exe:
            entry["error"] = "Claude CLI를 찾지 못했습니다."
        else:
            cwd = ROOT / "data"
            try:
                out = subprocess.run([exe, "--print", "reply ok", "--model", "haiku", "--max-turns", "1",
                                      "--output-format", "stream-json", "--verbose", "--no-session-persistence",
                                      "--strict-mcp-config", "--tools", ""],
                                     cwd=str(cwd), env=get_sanitized_env(), capture_output=True, text=True,
                                     timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
                parsed = parse_claude_stream(out.stdout)
                if parsed:
                    entry.update(parsed)
                else:
                    entry["error"] = "한도 정보를 받지 못했습니다" + (f" (종료 코드 {out.returncode})" if out.returncode else "")
            except subprocess.TimeoutExpired:
                entry["error"] = "확인 호출 시간 초과"
            except OSError as exc:
                entry["error"] = str(exc)[:200]
        cache = _read_cache()
        prev = cache.get("claude") or {}
        if entry.get("error") and prev.get("windows"):       # keep the last good numbers, mark them stale
            entry = {**prev, "error": entry["error"], "error_at": entry["checked_at"], "checked_at": prev.get("checked_at")}
        cache["claude"] = entry
        _write_cache(cache)
    finally:
        with _lock:
            _probing = False


def claude_limits(probe_minutes: float) -> Dict[str, Any]:
    """Cached Claude windows; starts a background probe when the cache is older than ``probe_minutes``."""
    global _probing
    entry = dict(_read_cache().get("claude") or {})
    if probe_minutes and probe_minutes > 0:
        last = max(entry.get("checked_at") or 0, entry.get("error_at") or 0)
        if _now() - last >= probe_minutes * 60:
            with _lock:
                start = not _probing
                _probing = _probing or start
            if start:
                threading.Thread(target=_probe_claude, name="claude-limit-probe", daemon=True).start()
        entry["probing"] = _probing
    else:
        entry["disabled"] = True
    return entry


# ------------------------------------------------------------------ Codex
def _latest_codex_files(limit: int = 6) -> List[Path]:
    if not CODEX_SESSIONS.is_dir():
        return []
    # sessions/YYYY/MM/DD/rollout-*.jsonl: walk the newest day folders only
    days = sorted((p for p in CODEX_SESSIONS.glob("*/*/*") if p.is_dir()), reverse=True)[:7]
    files = [f for d in days for f in d.glob("rollout-*.jsonl")]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:limit]


def _tail_lines(path: Path, max_bytes: int = 400_000) -> List[str]:
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        return fh.read().decode("utf-8", errors="replace").splitlines()


def codex_limits() -> Dict[str, Any]:
    for f in _latest_codex_files():
        try:
            lines = _tail_lines(f)
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            rl = (ev.get("payload") or {}).get("rate_limits") if isinstance(ev, dict) else None
            if not isinstance(rl, dict):
                continue
            windows = []
            for key in ("primary", "secondary"):
                w = rl.get(key)
                if not isinstance(w, dict):
                    continue
                mins = w.get("window_minutes") or 0
                label = "5시간" if mins == 300 else "주간" if mins == 10080 else "월" if mins >= 40000 else f"{mins}분"
                windows.append({"key": key, "label": label, "used_percent": w.get("used_percent"),
                                "resets_at": w.get("resets_at"), "window_minutes": mins})
            at = ev.get("timestamp")
            try:
                checked = datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
            except ValueError:
                checked = f.stat().st_mtime
            return {"windows": windows, "plan": rl.get("plan_type"), "checked_at": checked,
                    "status": "rejected" if rl.get("rate_limit_reached_type") else "allowed"}
    return {"error": "Codex 실행 기록에 한도 정보가 없습니다."}


# ------------------------------------------------------------------ Antigravity
def parse_agy_usage(stdout: str) -> Optional[Dict[str, Any]]:
    """`agy -p /usage --output-format json` -> {windows: [...]} (one window per model group bucket)."""
    try:
        data = json.loads((stdout or "").strip())
    except ValueError:
        return None
    groups = (((data or {}).get("command") or {}).get("data") or {}).get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return None
    short = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude·GPT"}
    windows = []
    for g in groups:
        for b in (g or {}).get("buckets") or []:
            rf = b.get("remaining_fraction")
            reset = None
            try:
                reset = datetime.fromisoformat(str(b.get("reset_time")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
            name = short.get(g.get("name"), g.get("name") or "?")
            windows.append({"key": b.get("id") or name, "label": name + (" 주간" if b.get("window") == "weekly" else ""),
                            "used_percent": round((1 - float(rf)) * 100, 1) if isinstance(rf, (int, float)) else None,
                            "resets_at": reset, "status": "rejected" if rf == 0 else "allowed"})
    return {"windows": windows} if windows else None


def _probe_agy() -> None:
    global _agy_probing
    try:
        from core.env_utils import resolve_cli, get_sanitized_env
        exe = resolve_cli("agy") or resolve_cli("antigravity")
        entry: Dict[str, Any] = {"checked_at": _now()}
        if not exe:
            entry["error"] = "Antigravity CLI를 찾지 못했습니다."
        else:
            try:
                out = subprocess.run([exe, "-p", "/usage", "--output-format", "json", "--mode", "plan"],
                                     cwd=str(ROOT / "data"), env=get_sanitized_env(), capture_output=True, text=True,
                                     timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
                parsed = parse_agy_usage(out.stdout)
                if parsed:
                    entry.update(parsed)
                else:
                    entry["error"] = "한도 정보를 받지 못했습니다" + (f" (종료 코드 {out.returncode})" if out.returncode else "")
            except subprocess.TimeoutExpired:
                entry["error"] = "확인 호출 시간 초과"
            except OSError as exc:
                entry["error"] = str(exc)[:200]
        cache = _read_cache()
        prev = cache.get("antigravity") or {}
        if entry.get("error") and prev.get("windows"):
            entry = {**prev, "error": entry["error"], "error_at": entry["checked_at"], "checked_at": prev.get("checked_at")}
        cache["antigravity"] = entry
        _write_cache(cache)
    finally:
        with _lock:
            _agy_probing = False


def _ledger_limit_hit() -> Dict[str, Any]:
    try:
        from core import usage
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        with usage._conn() as conn:
            r = conn.execute("SELECT started_at, ended_at, error_label, error_detail FROM ai_usage WHERE provider='GOOGLE' "
                             "AND error_code='USAGE_LIMIT' AND started_at >= ? ORDER BY id DESC LIMIT 1", (cutoff,)).fetchone()
            ok = conn.execute("SELECT MAX(started_at) FROM ai_usage WHERE provider='GOOGLE' AND status='OK'").fetchone()[0]
    except Exception:
        return {}
    if r is None or (ok and ok > (r["ended_at"] or r["started_at"])):
        return {}
    return {"limit_hit_at": (r["ended_at"] or r["started_at"]) + "Z",
            "limit_label": r["error_label"], "limit_detail": (r["error_detail"] or "")[:200]}


def antigravity_limits(probe_minutes: float) -> Dict[str, Any]:
    """Cached `/usage` buckets; background refresh like Claude. Falls back to the ledger's last limit hit."""
    global _agy_probing
    entry = dict(_read_cache().get("antigravity") or {})
    if probe_minutes and probe_minutes > 0:
        last = max(entry.get("checked_at") or 0, entry.get("error_at") or 0)
        if _now() - last >= probe_minutes * 60:
            with _lock:
                start = not _agy_probing
                _agy_probing = _agy_probing or start
            if start:
                threading.Thread(target=_probe_agy, name="agy-limit-probe", daemon=True).start()
        entry["probing"] = _agy_probing
    else:
        entry["disabled"] = True
    if not entry.get("windows"):
        entry.update(_ledger_limit_hit())
    return entry


def _minutes(opts: Dict[str, Any], key: str) -> float:
    try:
        return float(opts.get(key, DEFAULT_PROBE_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_PROBE_MINUTES


def snapshot(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = (config or {}).get("subscription_limits") or {}
    return {"now": _now(), "providers": {"ANTHROPIC": claude_limits(_minutes(opts, "claude_probe_minutes")),
                                         "OPENAI": codex_limits(),
                                         "GOOGLE": antigravity_limits(_minutes(opts, "antigravity_probe_minutes"))}}
