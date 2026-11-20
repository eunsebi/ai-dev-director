"""포크 2026-11-20: 대화형 Claude Code가 Codex·Antigravity에 읽기 전용 「두 번째 의견」만 묻는다(tools/second-opinion.py).

실제 CLI 대신 가짜 실행 파일을 TriadResearchRunner에 넘겨, 일회용 작업본·diff 첨부·쓰기 감지·한도 차단·입력 검증을 본다.
"""
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_CODEX = """#!{py}
import sys
prompt = sys.stdin.read()
marks = [m for m in ("QUESTION_MARK", "DIFF_MARK", "SECRET_MARK") if m in prompt]
print("## 결론\\ncodex 의견 " + ",".join(marks))
"""

FAKE_AGY = """#!{py}
import json, sys
msg = json.loads(sys.stdin.readline())
text = msg["message"]["content"][0]["text"]
print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": "## 결론\\nagy 의견 " + str("QUESTION_MARK" in text)}}}}))
"""

FAKE_WRITER = """#!{py}
import sys
sys.stdin.read()
open("hacked.txt", "w").write("x")
print("파일을 고쳤어요")
"""


def _script(dir_: Path, name: str, body: str) -> str:
    path = dir_ / name
    path.write_text(body.format(py=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def load_tool(monkeypatch, tmp_path, codex_body=FAKE_CODEX, agy_body=FAKE_AGY, limits=None):
    spec = importlib.util.spec_from_file_location("second_opinion", ROOT / "tools" / "second-opinion.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    exes = {"OPENAI": _script(bin_dir, "fake-codex", codex_body), "GOOGLE": _script(bin_dir, "fake-agy", agy_body)}
    real_runner = mod.TriadResearchRunner

    class Runner(real_runner):
        def __init__(self, **kw):
            super().__init__(executables=exes, **kw)

    monkeypatch.setattr(mod, "TriadResearchRunner", Runner)
    monkeypatch.setattr(mod, "DIRECTOR", tmp_path / "director")
    monkeypatch.setattr(mod, "PROJECTS", tmp_path / "projects.json")
    monkeypatch.setattr(mod, "probe_version", lambda exe: "fake 1.0")
    monkeypatch.setattr(mod, "limit_hits", lambda selected, force: {} if force else dict(limits or {}))
    return mod


def run(mod, monkeypatch, argv, stdin=""):
    monkeypatch.setattr("sys.argv", ["second-opinion", *argv])
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as ex:
        mod.main()
    return ex.value.code


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def make_project(tmp_path) -> Path:
    proj = tmp_path / "app"; proj.mkdir()
    git(proj, "init", "-q")
    (proj / "main.py").write_text("print('hi')\n")
    git(proj, "add", "main.py")
    git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return proj


def test_both_workers_answer_from_blank_workspace(monkeypatch, tmp_path, capsys):
    mod = load_tool(monkeypatch, tmp_path)
    assert run(mod, monkeypatch, ["ask", "both"], "QUESTION_MARK 이 캐시 설계가 괜찮을까?") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] and res["workspace"] == "빈 작업본"
    assert res["results"]["codex"]["ok"] and "QUESTION_MARK" in res["results"]["codex"]["answer"]
    assert res["results"]["agy"]["ok"] and "agy 의견 True" in res["results"]["agy"]["answer"]
    report = Path(res["report"])
    assert report.is_file() and "Codex" in report.read_text(encoding="utf-8")
    assert not (report.parent / "workspaces").exists()          # 일회용 작업본은 지움


def test_project_diff_is_attached_without_secret_files_and_project_is_untouched(monkeypatch, tmp_path, capsys):
    proj = make_project(tmp_path)
    (proj / "main.py").write_text("print('DIFF_MARK')\n")
    (proj / ".env").write_text("TOKEN=SECRET_MARK\n")
    git(proj, "add", "-N", ".env")                              # 추적 중인 비밀 파일 변경도 diff에서 빠져야 함
    before = git(proj, "status", "--porcelain")
    (tmp_path / "projects.json").write_text(json.dumps({"app": {"path": str(proj)}}))
    mod = load_tool(monkeypatch, tmp_path)
    assert run(mod, monkeypatch, ["ask", "codex", "--project", "app", "--with-diff"], "QUESTION_MARK 이 수정 맞아?") == 0
    res = json.loads(capsys.readouterr().out)
    answer = res["results"]["codex"]["answer"]
    assert "DIFF_MARK" in answer and "SECRET_MARK" not in answer
    assert res["with_diff"] and "agy" not in res["results"]
    assert git(proj, "status", "--porcelain") == before          # 원본 그대로
    assert git(proj, "worktree", "list").count("\n") == 1         # 일회용 worktree 정리됨


def test_without_diff_flag_uncommitted_change_is_not_sent(monkeypatch, tmp_path, capsys):
    proj = make_project(tmp_path)
    (proj / "main.py").write_text("print('DIFF_MARK')\n")
    mod = load_tool(monkeypatch, tmp_path)
    assert run(mod, monkeypatch, ["ask", "codex", "--project", str(proj)], "QUESTION_MARK 구조 의견 줘") == 0
    res = json.loads(capsys.readouterr().out)
    assert "DIFF_MARK" not in res["results"]["codex"]["answer"] and res["note"]


def test_worker_that_writes_is_rejected(monkeypatch, tmp_path, capsys):
    proj = make_project(tmp_path)
    mod = load_tool(monkeypatch, tmp_path, codex_body=FAKE_WRITER)
    assert run(mod, monkeypatch, ["ask", "codex", "--project", str(proj)], "QUESTION_MARK 이거 고쳐 줘") == 1
    res = json.loads(capsys.readouterr().out)
    r = res["results"]["codex"]
    assert not r["ok"] and r["error_kind"] == "READ_ONLY_VIOLATION" and r["next"]
    assert not (proj / "hacked.txt").exists()


def test_limit_guard_skips_a_worker_and_blocks_when_none_left(monkeypatch, tmp_path, capsys):
    mod = load_tool(monkeypatch, tmp_path, limits={"codex": "Codex 월 96% 사용"})
    assert run(mod, monkeypatch, ["ask", "both"], "QUESTION_MARK 의견 줘요") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["results"]["codex"]["error_kind"] == "LIMIT_GUARD" and res["results"]["agy"]["ok"]
    assert run(mod, monkeypatch, ["ask", "codex"], "QUESTION_MARK 의견 줘요") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "limit"
    assert run(mod, monkeypatch, ["ask", "codex", "--force"], "QUESTION_MARK 의견 줘요") == 0


def test_input_validation(monkeypatch, tmp_path, capsys):
    mod = load_tool(monkeypatch, tmp_path)
    assert run(mod, monkeypatch, ["ask", "codex"], "짧") == 2
    assert run(mod, monkeypatch, ["ask", "codex", "--with-diff"], "충분히 긴 질문입니다요") == 2
    assert run(mod, monkeypatch, ["ask", "codex", "--project", "no-such-alias"], "충분히 긴 질문입니다요") == 2
    plain = tmp_path / "plain"; plain.mkdir()
    assert run(mod, monkeypatch, ["ask", "codex", "--project", str(plain)], "충분히 긴 질문입니다요") == 2
    assert run(mod, monkeypatch, ["ask", "codex", "--timeout", "5"], "충분히 긴 질문입니다요") == 2


def test_check_reports_install_without_model_call(monkeypatch, tmp_path, capsys):
    mod = load_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "resolve_cli", lambda name: "/usr/bin/" + name if name == "codex" else None)
    assert run(mod, monkeypatch, ["check"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["workers"]["codex"]["installed"] and not res["workers"]["agy"]["installed"]
    assert not res["ok"] and "로그인" in res["note"]
