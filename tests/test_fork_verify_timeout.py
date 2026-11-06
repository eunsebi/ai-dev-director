"""[fork] Verification time limit is configurable (verification.timeout_sec, also per project) so an iOS simulator
test run is not cut at the hard-coded 120 s; default and clamping keep the upstream behaviour safe."""
import math

from core.bootstrap import configure_runtime
from core.verifier import Verifier


def test_timeout_default_and_clamp():
    assert Verifier.timeout_from(None) == 120.0
    assert Verifier.timeout_from({}) == 120.0
    assert Verifier.timeout_from({"verification": {"timeout_sec": "abc"}}) == 120.0
    assert Verifier.timeout_from({"verification": {"timeout_sec": math.nan}}) == 120.0
    assert Verifier.timeout_from({"verification": {"timeout_sec": 300}}) == 300.0
    assert Verifier.timeout_from({"verification": {"timeout_sec": 1}}) == 10.0
    assert Verifier.timeout_from({"verification": {"timeout_sec": 99999}}) == 1800.0


def test_project_override_reaches_the_timeout(tmp_path):
    cfg = {"verification": {"test_command": None},
           "projects": {str(tmp_path): {"verification": {"timeout_sec": 300, "test_command": "xcodebuild test"}}}}
    assert Verifier.timeout_from(configure_runtime(cfg, str(tmp_path))) == 300.0
    assert Verifier.timeout_from(configure_runtime(cfg, str(tmp_path / "other"))) == 120.0


def test_verify_passes_the_timeout_to_every_command(monkeypatch, tmp_path):
    seen = []

    def fake(cmd, cwd, timeout=120.0):
        seen.append((cmd, timeout))
        return {"command": cmd, "executed": bool(cmd), "passed": True, "output": ""}
    monkeypatch.setattr(Verifier, "_execute_cmd", staticmethod(fake))
    Verifier.verify(str(tmp_path), build_cmd="b", test_cmd="t", lint_cmd=None, timeout=300.0, git_checks=False)
    assert seen == [("b", 300.0), ("t", 300.0), (None, 300.0)]
