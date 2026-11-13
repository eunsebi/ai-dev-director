"""디스코드 접수함 (2026-11-12): 접수 전용 SSH 명령 → data/intake → 사람이 [시작]을 눌러야 미션."""
import json
import subprocess
import sys
import threading
import http.client
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "director-intake.py"


@pytest.fixture
def env(tmp_path, monkeypatch):
    proj = tmp_path / "my-app"
    proj.mkdir()
    pfile = tmp_path / "projects.json"
    pfile.write_text(json.dumps({"eodiya": {"path": str(proj), "name": "어디야"}, "Bad Key": {"path": "/x"}}), encoding="utf-8")
    intake = tmp_path / "intake"
    e = {"DIRECTOR_INTAKE_DIR": str(intake), "DIRECTOR_INTAKE_PROJECTS": str(pfile), "DIRECTOR_HOME": str(tmp_path)}
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    return SimpleNamespace(proj=proj, intake=intake, env=e)


def ssh(env, command, stdin=""):
    p = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(env.proj.parent), "SSH_ORIGINAL_COMMAND": command, **env.env})
    return p.returncode, json.loads(p.stdout)


def test_only_listed_commands_run(env):
    for cmd in ["", "bash", "submit", "status ../../etc/passwd", "list extra", "projects; rm -rf ~"]:
        code, res = ssh(env, cmd)
        assert code != 0 and not res["ok"], cmd
    code, res = ssh(env, "projects")
    assert code == 0 and res["projects"] == {"eodiya": "어디야"}


def test_submit_validates_project_text_and_secrets(env):
    assert ssh(env, "submit papercode", "로그인 고쳐줘")[1]["error"].startswith("허용되지 않은")
    assert not ssh(env, "submit eodiya", "짧음")[1]["ok"]
    assert not ssh(env, "submit eodiya", "x" * 4001)[1]["ok"]
    assert "비밀값" in ssh(env, "submit eodiya", json.dumps({"request": "토큰은 Bearer abcdefghijklmnopqrstuvwxyz0123 이야"}))[1]["error"]
    assert not env.intake.exists() or not list(env.intake.glob("REQ-*.json"))


def test_submit_then_status_waiting(env):
    code, res = ssh(env, "submit eodiya", json.dumps({"request": "친구 목록 정렬을 이름순으로 바꿔줘", "from": "discord:오빠<script>"}))
    assert code == 0 and res["ok"] and res["id"].startswith("REQ-")
    item = json.loads((env.intake / f"{res['id']}.json").read_text(encoding="utf-8"))
    assert item["status"] == "waiting" and item["project_path"] == str(env.proj.resolve()) and item["from"] == "discord:오빠script"
    code, st = ssh(env, f"status {res['id']}")
    assert st["status"] == "waiting" and "시작" in st["say"] and st["mission"] is None


def test_waiting_limit(env):
    for i in range(10):
        assert ssh(env, "submit eodiya", f"요청 번호 {i} 처리해줘")[1]["ok"]
    code, res = ssh(env, "submit eodiya", "열한 번째 요청입니다")
    assert code == 3 and "10개" in res["error"]


def test_start_requires_waiting_and_allowlist(env):
    from core import discord_intake
    _, res = ssh(env, "submit eodiya", "친구 목록 정렬을 이름순으로 바꿔줘")
    ident = res["id"]
    calls = []

    def fake_start(path, goal, **kw):
        calls.append((path, goal, kw))
        return {"status": "started", "mission_id": "MSN-TEST-1234"}

    r = discord_intake.start(ident, fake_start)
    assert r["status"] == "started"
    assert calls == [(str(env.proj), "친구 목록 정렬을 이름순으로 바꿔줘", {"source": "discord", "request_id": ident})]
    item = discord_intake.load(ident)
    assert item["status"] == "started" and item["mission_id"] == "MSN-TEST-1234"
    with pytest.raises(ValueError):
        discord_intake.start(ident, fake_start)          # 두 번 시작하지 않는다
    with pytest.raises(ValueError):
        discord_intake.load("../secret")


def test_start_rejected_when_project_removed_and_busy_keeps_waiting(env):
    from core import discord_intake
    _, res = ssh(env, "submit eodiya", "설정 화면 문구 다듬어줘")
    ident = res["id"]
    r = discord_intake.start(ident, lambda *a, **k: {"status": "busy", "error": "진행 중"})
    assert r["status"] == "busy" and discord_intake.load(ident)["status"] == "waiting"
    Path(env.env["DIRECTOR_INTAKE_PROJECTS"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="허용 목록"):
        discord_intake.start(ident, lambda *a, **k: {"status": "started"})
    assert discord_intake.discard(ident)["status"] == "discarded"
    with pytest.raises(ValueError):
        discord_intake.discard(ident)


@pytest.fixture
def http_server(env):
    from companion_server import CompanionHandler
    from dashboard_server import DashboardServer
    server = DashboardServer(('127.0.0.1', 0), CompanionHandler)
    server.bridge_token = 'test-token'
    server.bridge = SimpleNamespace(submit=lambda p: {}, list=lambda: [], allowed=set())
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield server
    server.shutdown(); server.server_close(); thread.join(timeout=2)


def req(server, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse(); data = r.read(); conn.close()
    return r.status, json.loads(data or b'{}')


def test_http_start_needs_token_and_lists(env, http_server):
    _, res = ssh(env, "submit eodiya", "친구 목록 정렬을 이름순으로 바꿔줘")
    code, listing = req(http_server, 'GET', '/ops/intake')
    assert code == 200 and listing['items'][0]['id'] == res['id']
    body = json.dumps({'id': res['id']})
    assert req(http_server, 'POST', '/ops/intake/start', body, {'Content-Type': 'application/json'})[0] == 403
    code, out = req(http_server, 'POST', '/ops/intake/discard', body, {'Content-Type': 'application/json', 'X-Solomon-Bridge': 'test-token'})
    assert code == 200 and out['status'] == 'discarded'


def test_same_waiting_request_is_not_duplicated(env):
    a = ssh(env, "submit eodiya", "설정 화면 문구 다듬어줘")[1]
    b = ssh(env, "submit eodiya", "설정 화면 문구 다듬어줘")[1]
    assert b["ok"] and b["duplicate"] and b["id"] == a["id"]
    assert len(list(env.intake.glob("REQ-*.json"))) == 1


def _git_proj(env):
    p = env.proj
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    (p / "docs" / "spec").mkdir(parents=True)
    (p / "docs" / "spec" / "06 장소.md").write_text("# 장소\n## 9. 결정 필요\n- 반경 1km?\n토큰 pcn_abcdefghijklmnopqrstuvwxyz0123456789\n", encoding="utf-8")
    (p / "CLAUDE.md").write_text("서버 주소 비밀\n", encoding="utf-8")
    (p / ".env").write_text("KEY=1\n", encoding="utf-8")
    (p / "GoogleService-Info.plist").write_text("<plist/>\n", encoding="utf-8")
    (p / "App.swift").write_text("let 반경 = 1000\n", encoding="utf-8")
    (p / ".gitignore").write_text("CLAUDE.md\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(p), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p), "add", "-f", ".env"], check=True)  # 실수로 추적된 비밀 파일도 막혀야 한다
    return p


def test_files_read_search_are_read_only_and_filtered(env):
    _git_proj(env)
    code, res = ssh(env, "files eodiya")
    assert code == 0 and "App.swift" in res["files"] and "docs/spec/06 장소.md" in res["files"]
    assert not any(f in res["files"] for f in ("CLAUDE.md", ".env", "GoogleService-Info.plist"))
    assert ssh(env, "files eodiya docs")[1]["files"] == ["docs/spec/06 장소.md"]
    code, res = ssh(env, "read eodiya 'docs/spec/06 장소.md'")
    assert code == 0 and "2\t## 9. 결정 필요" in res["content"] and "pcn_" not in res["content"] and "[가림]" in res["content"]
    assert ssh(env, "read eodiya 'docs/spec/06 장소.md' 3 1")[1]["content"] == "3\t- 반경 1km?"
    for bad in ["read eodiya CLAUDE.md", "read eodiya .env", "read eodiya ../projects.json", "read eodiya /etc/passwd",
                "read papercode App.swift", "files eodiya ../..", "search eodiya x", "read eodiya 'unclosed"]:
        code, res = ssh(env, bad)
        assert code != 0 and not res["ok"], bad
    code, res = ssh(env, "search eodiya 반경")
    assert code == 0 and any(h.startswith("App.swift:1:") for h in res["hits"]) and not any("CLAUDE" in h for h in res["hits"])
