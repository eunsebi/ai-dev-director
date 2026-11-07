import sys

from core.verifier import SKIPPED_STEP_NOTE, Verifier


def test_unconfigured_step_says_skipped_not_missing(tmp_path):
    res = Verifier._execute_cmd(None, tmp_path)
    assert res == {"command": None, "executed": False, "passed": True, "output": SKIPPED_STEP_NOTE}
    assert "No command specified" not in res["output"]
    assert "not a failure" in res["output"] and "build output" in res["output"]


def test_failed_build_with_skipped_tests_points_at_build(tmp_path):
    out = Verifier.verify(str(tmp_path), build_cmd=f"\"{sys.executable}\" -c \"import sys; print('error: boom'); sys.exit(65)\"",
                          git_checks=False)
    assert out["passed"] is False and out["summary"] == "Build: FAIL"
    assert "error: boom" in out["build"]["output"]
    assert out["test"]["output"] == SKIPPED_STEP_NOTE
