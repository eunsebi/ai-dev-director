"""
포크: 허용된 삭제도 「원본에 적용」에서 반영 (2026-11-17)

ClipGuard M2b-2에서 Builder가 지운 ReviewPlaceholderView.swift(ORDER.allowed_deletions)가 원본에 남았다.
작업본 전체 적용(apply_tree)은 삭제를 보고만 하고("작업본에서 지운 파일은 적용하지 않음") 적용하지 않았고,
커밋 단계는 없는 파일을 건너뛰었다. 이제 ORDER가 그 경로를 허용하고 원본 파일이 작업 시작 때와 같으면 지우고 커밋한다.
"""
import json

from core.bootstrap import snapshot
from core.result_manager import ResultManager
from tests.test_v312_delivery import RUNS
from tests.test_fork_apply_pipeline import _mission, _pipeline, git_out
from tests.test_v312_delivery import project, fake_run, cleanup_runs  # noqa: F401


def _ready(tmp_path, rid, allowed):
    proj = project(tmp_path)                                    # a.py, b.py committed
    wt = fake_run(proj, rid, snapshot(str(proj)))
    (wt / "a.py").write_text("a = 2\n", encoding="utf-8")
    (wt / "b.py").unlink()                                      # the Builder deleted b.py
    (RUNS / rid / "order.json").write_text(json.dumps({"goal": "g", "allowed_deletions": allowed}), encoding="utf-8")
    return proj, wt


def test_allowed_deletion_is_applied_committed_and_reverted_on_failure(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-DEL1", "MSN-TEST-DEL1"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid, ["b.py"])
    tree = ResultManager().tree_manifest(rid)
    assert {"path": "b.py", "type": "D", "source": None, "sha256": None, "size": 0, "deletion": True} in tree["manifest"]
    assert tree["deleted"] == []
    store, calls = _mission(proj, rid, mid), {}
    p = _pipeline(proj, {"manual_checks": [{"name": "⌘U"}], "cleanup": False}, store, calls)
    p.start(mid, rid, background=False)
    assert not (proj / "b.py").exists()                         # applied before the manual check
    p.answer_manual(mid, True, background=False)
    d = store.load(mid)["delivery"]
    assert d["status"] == "DONE", d
    assert "작업본에서 지운 파일은 적용하지 않음" not in json.dumps(d, ensure_ascii=False)
    assert git_out(proj, "show", "--name-status", "--format=", "HEAD").split() == ["M", "a.py", "D", "b.py"]


def test_manual_failure_restores_the_deleted_file(tmp_path, cleanup_runs):
    rid, mid = "RUN-TEST-DEL2", "MSN-TEST-DEL2"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid, ["b.py"])
    store, calls = _mission(proj, rid, mid), {}
    p = _pipeline(proj, {"manual_checks": [{"name": "⌘U"}], "cleanup": False}, store, calls)
    p.start(mid, rid, background=False)
    assert not (proj / "b.py").exists()
    p.answer_manual(mid, False, note="깨짐", background=False)
    assert (proj / "b.py").read_text(encoding="utf-8") == "b = 1\n"     # restored from the apply backup
    assert (proj / "a.py").read_text(encoding="utf-8") == "a = 1\n"


def test_unlisted_deletion_is_only_reported(tmp_path, cleanup_runs):
    rid = "RUN-TEST-DEL3"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid, [])
    tree = ResultManager().tree_manifest(rid)
    assert tree["deleted"] == ["b.py"] and all(m["path"] != "b.py" for m in tree["manifest"])


def test_deletion_of_a_file_the_user_changed_is_a_conflict(tmp_path, cleanup_runs):
    rid = "RUN-TEST-DEL4"; cleanup_runs.append(rid)
    proj, _ = _ready(tmp_path, rid, ["b.py"])
    (proj / "b.py").write_text("b = 99  # the user's edit\n", encoding="utf-8")
    tree = ResultManager().tree_manifest(rid)
    assert all(m["path"] != "b.py" for m in tree["manifest"])
    assert any(c["path"] == "b.py" and "deletion skipped" in c["reason"] for c in tree["conflicts"])
