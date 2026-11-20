"""포크 2026-11-20: 미션이 끝나면 결과물 작업본에서 명령 검사 + Claude 검수 보고서를 만들어 디스코드로 보낸다.
사람이 Xcode ⌘U·⌘R을 누르고 Claude Code에 이상 여부를 따로 묻던 단계를 줄인다."""
import json
import subprocess
import sys
from pathlib import Path

from core import pre_apply_review as par
from core.mission import MissionRunner, MissionStore
from schemas.order import OrderSchema
from tests.test_fork_discord_mission_alerts import sent  # noqa: F401  (fixture)
from tests.test_v306_head_proxy_autonomy import ORDER, mission_factory


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def test_checks_restore_files_a_check_rewrote_and_report_new_ones(tmp_path):
    wt = _git_repo(tmp_path / "wt")
    (wt / "a.txt").write_text("changed by the AI\n")                       # 결과물의 변경
    script = (f"{sys.executable} -c \"import pathlib; pathlib.Path('a.txt').write_text('rewritten by xcode'); "
              f"pathlib.Path('junk.txt').write_text('x')\"")
    checks = [{"name": "검사1", "cmd": script}, {"name": "실패", "cmd": "exit 3"},
              {"name": "선택", "cmd": "exit 1", "optional": True}]
    results, warnings = par.run_checks(checks, str(wt), str(tmp_path), str(tmp_path / "r"))
    assert [r["status"] for r in results] == ["ok", "fail", "warn"]
    assert (wt / "a.txt").read_text() == "changed by the AI\n"             # 되돌림
    assert any("되돌렸어요" in w and "a.txt" in w for w in warnings)
    assert any("새 파일" in w and "junk.txt" in w for w in warnings)


def test_placeholders_and_overall_verdict(tmp_path):
    cmd = par._expand(["{director}/x", "{worktree}", "{report_dir}"], "/wt", "/p", "/r")
    assert cmd[0].endswith("/x") and cmd[1:] == ["/wt", "/r"]
    ok = [{"name": "t", "status": "ok"}]
    assert par.overall(ok, {"verdict": "ok"}, [], []) == "ok"
    assert par.overall(ok, {"verdict": "skipped"}, [], []) == "warn"
    assert par.overall(ok, {"verdict": "ok"}, [], [{"path": "x"}]) == "warn"
    assert par.overall([{"name": "t", "status": "fail"}], {"verdict": "ok"}, [], []) == "fail"
    assert par.overall(ok, {"verdict": "fail"}, [], []) == "fail"


def test_parse_claude_answer():
    env = json.dumps({"result": 'blah\n{"verdict":"warn","summary":"요약","concerns":["a.swift:3 — x"],"manual_checks":["눌러 보기"]}'})
    ans = par.parse_claude_answer(env)
    assert ans["verdict"] == "warn" and ans["concerns"] == ["a.swift:3 — x"] and ans["manual_checks"] == ["눌러 보기"]
    assert par.parse_claude_answer("garbage")["verdict"] == "skipped"


def test_file_diffs_show_changes_and_truncate(tmp_path):
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "a.txt").write_text("one\n")
    src = tmp_path / "wt_a.txt"; src.write_text("two\n")
    new = tmp_path / "wt_b.txt"; new.write_text("new\n" * 10)
    text = par.file_diffs([{"path": "a.txt", "type": "M", "source": str(src)},
                           {"path": "b.txt", "type": "A", "source": str(new)},
                           {"path": "c.txt", "type": "D", "source": None}], str(proj))
    assert "-one" in text and "+two" in text and "+++ b/b.txt" in text and "(삭제)" in text
    short = par.file_diffs([{"path": "b.txt", "type": "A", "source": str(new)}] * 3, str(proj), limit=60)
    assert "자름" in short


def test_review_runs_after_a_done_mission_and_is_sent(tmp_path, monkeypatch, sent):
    proj = str(tmp_path / "proj")
    plans = [{"status": "DONE", "summary": "done", "reason": "", "order": None}]
    factory = mission_factory(monkeypatch, plans, [])
    cfg = {"projects": {proj: {"pre_apply_review": {"checks": [{"name": "테스트", "cmd": "true"}],
                                                    "claude": {"model": "sonnet"}}}},
           "remote_access": {"hosts": ["mac.example.ts.net"]}}

    def with_cfg():
        orch = factory()
        orch.config.update(cfg)
        return orch
    seen = {}

    def fake_claude(facts, diff_text, worktree, report_dir, opts, config):
        seen.update(facts=facts, diff=diff_text, opts=opts)
        return {"verdict": "ok", "summary": "목표대로 됐어요.", "concerns": [], "manual_checks": ["첫 화면 확인"]}
    monkeypatch.setattr(par, "claude_review", fake_claude)
    monkeypatch.setattr(par, "ROOT", tmp_path / "director")
    runner = MissionRunner(with_cfg, MissionStore(tmp_path / "missions"))
    m = runner.create(proj, "double", OrderSchema(**ORDER), config={"autonomy": {"auto_apply": "off"}})
    done = runner.run(m["mission_id"])
    assert done["status"] == "DONE"
    review = done["review"]
    assert review["status"] == "ok" and Path(review["report"]).is_file()
    assert seen["opts"] == {"model": "sonnet"} and seen["facts"]["checks"][0]["status"] == "ok"
    assert any(f["path"] == "feature.py" for f in seen["facts"]["files"])
    titles = [t for t, _ in sent]
    assert "🔎 검수 시작 · proj" in titles and titles[-1] == "🟢 검수 보고서 · 적용 권장 · proj"
    body = sent[-1][1]
    assert "https://mac.example.ts.net" in body and "첫 화면 확인" in body
    report = Path(review["report"]).read_text()
    assert "적용 권장" in report and "feature.py" in report


def test_failed_review_blocks_auto_apply(tmp_path, monkeypatch, sent):
    proj = str(tmp_path / "proj")
    plans = [{"status": "DONE", "summary": "done", "reason": "", "order": None}]
    factory = mission_factory(monkeypatch, plans, [])
    cfg = {"projects": {proj: {"pre_apply_review": {"checks": [{"name": "테스트", "cmd": "exit 1"}], "claude": False}}}}

    def with_cfg():
        orch = factory()
        orch.config.update(cfg)
        return orch
    monkeypatch.setattr(par, "ROOT", tmp_path / "director")
    runner = MissionRunner(with_cfg, MissionStore(tmp_path / "missions"))
    m = runner.create(proj, "double", OrderSchema(**ORDER), config={"autonomy": {"auto_apply": "on_done"}})
    done = runner.run(m["mission_id"])
    assert done["review"]["status"] == "fail"
    assert not (tmp_path / "proj" / "feature.py").exists()                 # 자동 적용 안 함
    assert any("자동 적용 안 함" in t for t, _ in sent)


def test_no_review_without_config(tmp_path, monkeypatch, sent):
    plans = [{"status": "DONE", "summary": "done", "reason": "", "order": None}]
    runner = MissionRunner(mission_factory(monkeypatch, plans, []), MissionStore(tmp_path / "missions"))
    m = runner.create(str(tmp_path / "proj"), "double", OrderSchema(**ORDER), config={"autonomy": {"auto_apply": "off"}})
    done = runner.run(m["mission_id"])
    assert "review" not in done and not any("검수" in t for t, _ in sent)
