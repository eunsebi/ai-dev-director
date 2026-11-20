"""포크 2026-11-20: Claude Code가 디스코드를 거치지 않고 맥 안의 Director에 바로 접수·시작한다(tools/director-submit.py)."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_tool(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("director_submit", ROOT / "tools" / "director-submit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proj = tmp_path / "app"; proj.mkdir()
    pj = tmp_path / "projects.json"
    pj.write_text(json.dumps({"app": {"path": str(proj), "name": "앱"}}))
    monkeypatch.setattr(mod, "PROJECTS", pj)
    return mod, proj


def run(mod, monkeypatch, argv, stdin=""):
    monkeypatch.setattr("sys.argv", ["director-submit", *argv])
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as ex:
        mod.main()
    return ex.value.code


def test_start_sends_a_stable_request_from_claude_code(monkeypatch, tmp_path, capsys):
    mod, proj = load_tool(monkeypatch, tmp_path)
    calls = []

    def fake_call(method, path, body=None):
        calls.append((method, path, body))
        return 202, {"status": "started", "mission_id": "MSN-1"}
    monkeypatch.setattr(mod, "call", fake_call)
    assert run(mod, monkeypatch, ["start", "app"], "어디야 F12 기능 만들어줘. 수정할 폴더: Eodiya/") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["mission_id"] == "MSN-1" and "시작" in out["say"]
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/bridge/missions")
    assert body["source"] == "claude-code" and body["project_path"] == str(proj.resolve())
    assert body["request_id"].startswith("cc-")
    # 같은 지시문은 같은 request_id(서버가 중복 미션을 만들지 않음)
    run(mod, monkeypatch, ["start", "app"], "어디야 F12 기능 만들어줘. 수정할 폴더: Eodiya/")
    assert calls[1][2]["request_id"] == body["request_id"]


def test_rejects_unknown_project_and_short_goal(monkeypatch, tmp_path, capsys):
    mod, _ = load_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "call", lambda *a, **k: pytest.fail("should not call"))
    assert run(mod, monkeypatch, ["start", "nope-not-here"], "충분히 긴 지시문입니다") == 2
    assert run(mod, monkeypatch, ["start", "app"], "짧") == 2


def test_busy_is_reported_as_failure(monkeypatch, tmp_path, capsys):
    mod, _ = load_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "call", lambda *a, **k: (409, {"status": "busy", "error": "busy"}))
    assert run(mod, monkeypatch, ["start", "app"], "충분히 긴 지시문입니다") == 1
    assert "다른 미션" in json.loads(capsys.readouterr().out)["say"]


def test_bridge_accepts_claude_code_as_source():
    text = (ROOT / "companion_server.py").read_text(encoding="utf-8")
    assert "source = 'claude-code' if payload.get('source') == 'claude-code' else 'solomon'" in text
