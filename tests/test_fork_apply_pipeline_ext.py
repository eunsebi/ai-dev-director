"""fork 2026-11-09: the extended 「원본에 적용」 pipeline (core/apply_pipeline.py, core/string_catalog.py).

prechecks in the worktree → String Catalog check → apply → checks → ⌘U → commit (co-author, CLAUDE.md guard,
include_if_changed) → graphify → push → devlog → cleanup → record (HEAD Claude, memory_append only for that call).
"""
import json
import subprocess
import sys

import pytest

from core import string_catalog as sc
from core.apply_pipeline import Pipeline, plan_steps, _outside_handoff_changed
from core.result_manager import ResultManager, RESULT_APPLIED
from tests.test_fork_apply_pipeline import _mission, _pipeline, _ready, git_out
from tests.test_v312_delivery import project, fake_run, cleanup_runs  # noqa: F401


def _sub(tmp_path, name="two"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_plan_has_every_stage_in_order():
    pipe = {"prechecks": [{"name": "로직 테스트", "cmd": ["true"]}],
            "string_catalog": {"catalog": "L.xcstrings", "derived_data": "/x"},
            "checks": [{"cmd": ["true"]}], "manual_checks": [{"name": "⌘U"}],
            "commit": {"co_authors": []}, "graphify": True, "push": True, "devlog": {"repo": "r"},
            "record": {"memory": "m"}}
    assert [s["kind"] for s in plan_steps(pipe)] == [
        "precheck", "catalog", "apply", "check", "manual", "commit", "graphify", "push", "devlog", "cleanup", "record"]
    assert "devlog" not in [s["kind"] for s in plan_steps({"devlog": {"repo": "r"}})]     # devlog needs push
    assert [s["kind"] for s in plan_steps({"commit": {"enabled": False}, "cleanup": False})] == ["apply"]


def test_failed_precheck_never_touches_the_project_and_goes_back_to_head(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PX1", "MSN-TEST-PX1"; cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"prechecks": [{"name": "로직 테스트", "cmd": [sys.executable, "-c",
                                                      "import sys; print('MapModelTests failed'); sys.exit(1)"]}]}
    _pipeline(proj, pipe, store, calls).start(mid, rid, background=False)
    m = store.load(mid)
    assert m["delivery"]["status"] == "REWORK"
    assert [s["status"] for s in m["delivery"]["steps"]][:2] == ["failed", "pending"]
    assert (proj / "a.py").read_text(encoding="utf-8") == "a = 1\n" and not (proj / "new.py").exists()
    fb = m["steps"][-1]["result"]["post_apply_failure"]
    assert fb.startswith("PRE-APPLY CHECK FAILED") and "MapModelTests failed" in fb
    assert calls["extend"] and "적용 전" in calls["extend"][0][1]
    assert "applied" not in m                                                  # nothing was applied
    assert ResultManager().result_state(rid).get("state") != RESULT_APPLIED


def test_precheck_runs_in_the_worktree_and_may_not_write_into_it(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PX2", "MSN-TEST-PX2"; cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    out = tmp_path / "seen.txt"
    script = ("import os, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(os.getcwd() + '|' + "
              "os.environ['DIRECTOR_PROJECT'] + '|' + sys.argv[2])")
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"prechecks": [{"name": "검수", "cmd": [sys.executable, "-c", script, str(out), "{worktree}"]}],
            "commit": False, "cleanup": False}
    _pipeline(proj, pipe, store, calls).start(mid, rid, background=False)
    assert store.load(mid)["delivery"]["status"] == "DONE"
    cwd, project_env, arg = out.read_text().split("|")
    assert cwd == str(wt.resolve()) or cwd == str(wt)
    assert project_env == str(proj) and arg == str(wt)

    rid2, mid2 = "RUN-TEST-PX3", "MSN-TEST-PX3"; cleanup_runs.append(rid2)
    proj2, wt2 = _ready(_sub(tmp_path), rid2)
    store2, calls2 = _mission(proj2, rid2, mid2), {}
    dirty = {"prechecks": [{"name": "더러운 검수", "cmd": [sys.executable, "-c", "open('junk.o','w').write('x')"]}]}
    _pipeline(proj2, dirty, store2, calls2).start(mid2, rid2, background=False)
    d = store2.load(mid2)["delivery"]
    assert d["status"] == "REWORK" and "작업본 파일을 바꿨습니다" in d["steps"][0]["detail"]
    assert not (proj2 / "junk.o").exists()


# ---------------------------------------------------------------- String Catalog
def _catalog(path, keys, stale=()):
    strings = {k: {} for k in keys}
    strings.update({k: {"extractionState": "stale"} for k in stale})
    strings["직접 넣은 키"] = {"extractionState": "manual"}
    path.write_text(json.dumps({"sourceLanguage": "ko", "strings": strings, "version": "1.0"}, ensure_ascii=False), encoding="utf-8")


def _stringsdata(dd, source, keys, name="V"):
    f = dd / "Build" / "Intermediates.noindex" / "App.build" / f"{name}.stringsdata"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"source": str(source), "version": 2, "tables": {"Localizable": [
        {"key": k, "comment": "", "location": {"startingLine": i + 1, "startingColumn": 1}} for i, k in enumerate(keys)]}},
        ensure_ascii=False), encoding="utf-8")


def test_string_catalog_compare_and_ternary(tmp_path):
    root = tmp_path / "wt"; (root / "App").mkdir(parents=True)
    src = root / "App" / "V.swift"
    src.write_text('let a = Text(isNew ? "장소 추가" : "장소 편집")\n// x ? "가" : "나"\nlet b = flag ? "a" : "b"\n', encoding="utf-8")
    other = tmp_path / "proj" / "App" / "V.swift"; other.parent.mkdir(parents=True); other.write_text("")
    dd = tmp_path / "dd"
    _stringsdata(dd, src, ["장소 추가", "장소 편집", "새 문구"])
    _stringsdata(dd, other, ["다른 작업본"], name="Other")                # another tree's build: ignored
    files = sc.find_stringsdata(str(dd), str(root))
    assert len(files) == 1
    _catalog(root / "L.xcstrings", ["장소 추가", "장소 편집", "안 쓰는 문구"], stale=["옛 문구"])
    rep = sc.compare(root / "L.xcstrings", sc.extracted_keys(files))
    assert rep["missing"] == ["새 문구"] and rep["stale_marked"] == ["옛 문구"] and rep["unused"] == ["안 쓰는 문구"]
    assert sc.ternary_lines(str(root), ["App/V.swift"]) == ["App/V.swift:1"]     # comment and non-Korean ignored
    text = sc.summarize({**rep, "ternary": ["App/V.swift:1"]})
    assert "새 문구" in text and "stale 2개" in text and "if/else" in text


def _catalog_case(tmp_path, cleanup_runs, rid, mid, fail_on):
    cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    (wt / "App").mkdir()
    (wt / "App" / "V.swift").write_text('Text(on ? "켜짐" : "꺼짐")\n', encoding="utf-8")
    _catalog(wt / "L.xcstrings", ["켜짐"])
    dd = tmp_path / "dd"
    _stringsdata(dd, wt / "App" / "V.swift", ["켜짐", "꺼짐"])
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"string_catalog": {"catalog": "L.xcstrings", "derived_data": str(dd), "fail_on": fail_on},
            "commit": False, "cleanup": False}
    _pipeline(proj, pipe, store, calls).start(mid, rid, background=False)
    return proj, store.load(mid)


def test_catalog_reports_as_warning_by_default(tmp_path, cleanup_runs):
    proj, m = _catalog_case(tmp_path, cleanup_runs, "RUN-TEST-PX4", "MSN-TEST-PX4", [])
    d = m["delivery"]
    assert d["status"] == "DONE"                                               # a report, not a warning of the run
    cat = d["steps"][0]
    assert cat["kind"] == "catalog" and cat["status"] == "warn"
    assert "꺼짐" in cat["detail"] and "App/V.swift:1" in cat["detail"]
    assert d["catalog"]["missing"] == ["꺼짐"]
    assert (proj / "App" / "V.swift").exists()                                 # applied


def test_catalog_fail_on_missing_sends_back_before_apply(tmp_path, cleanup_runs):
    proj, m = _catalog_case(tmp_path, cleanup_runs, "RUN-TEST-PX5", "MSN-TEST-PX5", ["missing"])
    assert m["delivery"]["status"] == "REWORK"
    assert not (proj / "App" / "V.swift").exists()
    assert "꺼짐" in m["steps"][-1]["result"]["post_apply_failure"]


# ---------------------------------------------------------------- commit, graphify, push, devlog
def test_commit_message_co_author_claude_md_guard_and_catalog_rewritten_during_cmd_u(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-PX6", "MSN-TEST-PX6"; cleanup_runs.append(rid)
    proj, wt = _ready(tmp_path, rid)
    (proj / "L.xcstrings").write_text("{}\n"); (proj / "dirty.txt").write_text("1\n")
    _git(proj, "add", "L.xcstrings", "dirty.txt"); _git(proj, "commit", "-qm", "catalog")
    (proj / "dirty.txt").write_text("user edit\n")                             # the user's own pending change
    (wt / "CLAUDE.md").write_text("secret\n")                                  # must never be committed
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"manual_checks": [{"name": "⌘U"}], "cleanup": False,
            "commit": {"co_authors": ["Claude Opus 5.5 <noreply@anthropic.com>"],
                       "include_if_changed": ["L.xcstrings", "dirty.txt"]}}
    p = _pipeline(proj, pipe, store, calls)
    p.start(mid, rid, background=False)
    (proj / "L.xcstrings").write_text('{"strings": {}}\n')                    # Xcode re-formatted it during ⌘U
    p.answer_manual(mid, True, background=False)
    d = store.load(mid)["delivery"]
    assert d["status"] == "DONE", d
    files = set(git_out(proj, "show", "--name-only", "--format=", "HEAD").split())
    assert files == {"a.py", "new.py", "L.xcstrings"}                          # not CLAUDE.md, not dirty.txt
    msg = git_out(proj, "log", "-1", "--format=%B")
    assert msg.startswith("a를 2로 바꾸고 new.py를 추가한다\n\n끝\nDirector 미션 MSN-TEST-PX6")
    assert msg.rstrip().endswith("Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    assert "dirty.txt" in git_out(proj, "status", "--porcelain")


def test_push_and_devlog_only_with_a_remote(tmp_path, cleanup_runs, monkeypatch):
    rid, mid = "RUN-TEST-PX7", "MSN-TEST-PX7"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid)
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"cleanup": False, "push": True, "devlog": {"repo": "my-app"}, "graphify": True}
    _pipeline(proj, pipe, store, calls).start(mid, rid, background=False)
    st = {s["kind"]: s for s in store.load(mid)["delivery"]["steps"]}
    assert st["commit"]["status"] == "ok"
    assert st["push"]["status"] == "skipped" and "원격" in st["push"]["detail"]
    assert st["devlog"]["status"] == "skipped" and st["graphify"]["status"] == "skipped"

    rid2, mid2 = "RUN-TEST-PX8", "MSN-TEST-PX8"; cleanup_runs.append(rid2)
    proj2, _ = _ready(_sub(tmp_path), rid2)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    branch = _git(proj2, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _git(proj2, "remote", "add", "origin", str(bare)); _git(proj2, "push", "-q", "-u", "origin", branch)
    bindir = tmp_path / "bin"; bindir.mkdir()
    log = tmp_path / "ssh.txt"
    (bindir / "ssh").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {log}\necho 초안 만듦\n"); (bindir / "ssh").chmod(0o755)
    (proj2 / "graphify-out").mkdir(); (proj2 / "graphify-out" / "graph.json").write_text("{}")
    (bindir / "graphify").write_text(f"#!/bin/sh\necho \"$@\" > {tmp_path / 'g.txt'}\n"); (bindir / "graphify").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:" + __import__("os").environ["PATH"])
    store2, calls2 = _mission(proj2, rid2, mid2), {}
    _pipeline(proj2, {**pipe, "graphify": {"executable": str(bindir / "graphify")}}, store2, calls2).start(mid2, rid2, background=False)
    d = store2.load(mid2)["delivery"]
    st = {s["kind"]: s for s in d["steps"]}
    assert d["status"] == "DONE", d
    assert st["push"]["status"] == "ok" and _git(bare, "rev-parse", branch).strip() == d["commit"]
    assert (tmp_path / "g.txt").read_text().strip() == "update ."
    args = log.read_text().splitlines()
    assert args[:3] == ["-o", "BatchMode=yes", "truenas"]
    assert args[3] == "./devlog my-app 'a를 2로 바꾸고 new.py를 추가한다'"            # quoted for the remote shell


# ---------------------------------------------------------------- record (HEAD Claude)
def test_handoff_guard():
    base = "# 규칙\n- a\n\n## 작업 현황 (handoff)\n- 옛 상태\n\n## graphify\n- g\n"
    assert _outside_handoff_changed(base, base.replace("옛 상태", "새 상태\n- 남은 할 일")) == ""
    assert _outside_handoff_changed(base, base.replace("- a", "- b")) == "섹션 밖 내용이 달라짐"
    assert _outside_handoff_changed("# 규칙\n", "# 규칙\n\n## 작업 현황 (handoff)\n- 새\n") == ""
    assert _outside_handoff_changed(base, "# 규칙\n- a\n\n## graphify\n- g\n") == "handoff 섹션이 사라짐"


def _fake_claude(tmp_path, edit_py):
    fake = tmp_path / "claude"
    args_file = tmp_path / "args.txt"
    fake.write_text("#!/bin/sh\n"
                    f"echo \"$@\" > {args_file}\n"
                    "cat > /dev/null\n"
                    f"{sys.executable} -c {json.dumps(edit_py)}\n"
                    "echo '{\"result\": \"{\\\"memory\\\":\\\"ok\\\",\\\"claude_md\\\":\\\"ok\\\"}\"}'\n", encoding="utf-8")
    fake.chmod(0o755)
    return fake, args_file


def test_record_opens_only_memory_append_and_reverts_edits_outside_handoff(tmp_path, cleanup_runs, monkeypatch):
    import core.env_utils as eu
    monkeypatch.setenv("DIRECTOR_CLAUDE_JSON", str(tmp_path / "claude.json"))
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {"claude-memory": {"type": "http", "url": "http://x"},
                                                                      "other": {"type": "http", "url": "http://y"}}}))
    rid, mid = "RUN-TEST-PX9", "MSN-TEST-PX9"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid)
    original = "# 규칙\n- 지키기\n\n## 작업 현황 (handoff)\n- 옛\n"
    (proj / "CLAUDE.md").write_text(original, encoding="utf-8")
    bad = f"import pathlib; p = pathlib.Path({str(proj / 'CLAUDE.md')!r}); p.write_text(p.read_text().replace('지키기', '망침'))"
    fake, args_file = _fake_claude(tmp_path, bad)
    monkeypatch.setattr(eu, "resolve_cli", lambda name: str(fake))
    store, calls = _mission(proj, rid, mid), {}
    pipe = {"cleanup": False, "record": {"memory": "eodiya-project", "device": "mac"}}
    p = _pipeline(proj, pipe, store, calls)
    cfg = p.hooks["load_config"]()
    cfg["head"] = {"allowed_tools": ["mcp__claude-memory__memory_index", "mcp__claude-memory__memory_read"]}
    p.start(mid, rid, background=False)
    d = store.load(mid)["delivery"]
    rec = [s for s in d["steps"] if s["kind"] == "record"][0]
    assert rec["status"] == "failed" and "되돌렸습니다" in rec["detail"]
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == original
    assert d["status"] == "DONE_WITH_WARNINGS"
    args = args_file.read_text()
    allowed = args.split("--allowedTools ")[1].split(" ")[0].split(",")
    assert "mcp__claude-memory__memory_append" in allowed and "mcp__claude-memory__memory_write" not in allowed
    assert "mcp__claude-memory__memory_index" in allowed
    assert "mcp__claude-memory__memory_write" in args.split("--disallowedTools ")[1]
    assert "--strict-mcp-config" in args and "--mcp-config" in args

    rid2, mid2 = "RUN-TEST-PX10", "MSN-TEST-PX10"; cleanup_runs.append(rid2)
    proj2, _ = _ready(_sub(tmp_path), rid2)
    (proj2 / "CLAUDE.md").write_text(original, encoding="utf-8")
    good = f"import pathlib; p = pathlib.Path({str(proj2 / 'CLAUDE.md')!r}); p.write_text(p.read_text().replace('- 옛', '- 새 상태'))"
    fake2, _ = _fake_claude(tmp_path / "two", good)
    monkeypatch.setattr(eu, "resolve_cli", lambda name: str(fake2))
    store2, calls2 = _mission(proj2, rid2, mid2), {}
    _pipeline(proj2, pipe, store2, calls2).start(mid2, rid2, background=False)
    d2 = store2.load(mid2)["delivery"]
    assert d2["status"] == "DONE", d2
    assert "새 상태" in (proj2 / "CLAUDE.md").read_text(encoding="utf-8")
