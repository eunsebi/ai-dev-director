"""
포크: 토큰·대기 줄이기 (2026-11-08)

- Director가 부르는 Claude CLI는 MCP 서버·스킬·훅·기본 도구 없이 실행한다. HEAD 계획 단계는 허용된 MCP 서버(공유 메모리)만
  임시 --mcp-config(0600, 호출 뒤 삭제)로 연다.
- Builder 단계의 시간 초과는 같은 프롬프트로 다시 보내지 않는다(Director가 바로 다른 제공자로 넘긴다).
- 첫 코딩 프롬프트: ORDER가 이름을 댄 파일 + 그 파일들이 쓰는 타입의 파일만 보내고, 나머지는 경로만 알린다.
  바이너리·에셋 카탈로그·잠금 파일·CLAUDE.md는 보내지 않는다.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from agents.proposal_builder import ProposalBuilder
from core import claude_cli
from core.research_runner import ResearchProviderCallError, TriadResearchRunner
from schemas.order import OrderSchema
from tests.test_v306_head_proxy_autonomy import git_project


@pytest.fixture(autouse=True)
def lean_on(monkeypatch):
    monkeypatch.delenv("DIRECTOR_CLAUDE_LEAN", raising=False)
    claude_cli.configure({})
    yield
    claude_cli.configure({})


def test_static_args_disable_tools_mcp_skills_and_settings():
    args = claude_cli.static_args("")
    assert args[:4] == ["--strict-mcp-config", "--disable-slash-commands", "--setting-sources", ""]
    assert args[-2:] == ["--tools", ""]
    assert claude_cli.static_args("Read,Grep,Glob")[-1] == "Read,Grep,Glob"


def test_lean_can_be_switched_off(monkeypatch):
    claude_cli.configure({"claude_cli": {"lean": False}})
    assert claude_cli.static_args() == []
    claude_cli.configure({})
    monkeypatch.setenv("DIRECTOR_CLAUDE_LEAN", "0")
    assert claude_cli.static_args() == []


def test_lean_args_open_only_the_allowed_mcp_server_in_a_private_temp_file(tmp_path, monkeypatch):
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "claude-memory": {"type": "http", "url": "https://m.example/mcp", "headers": {"Authorization": "secret"}},
        "rea": {"command": "rea"}}}))
    monkeypatch.setenv("DIRECTOR_CLAUDE_JSON", str(cfg))
    tools = ["mcp__claude-memory__memory_index", "mcp__claude-memory__memory_read"]
    with claude_cli.lean_args(tools) as args:
        path = Path(args[args.index("--mcp-config") + 1])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert list(json.loads(path.read_text())["mcpServers"]) == ["claude-memory"]
    assert not path.exists()
    with claude_cli.lean_args(None) as args:
        assert "--mcp-config" not in args and args[-2:] == ["--tools", ""]


def test_claude_builder_command_is_lean():
    cmd, _, desc = TriadResearchRunner()._command("ANTHROPIC", "/bin/claude", "p")
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--tools") + 1] == "" and desc.endswith("(lean)")


def test_building_timeout_is_not_retried_with_the_same_prompt(monkeypatch):
    runner = TriadResearchRunner()
    calls = []

    def once(domain, prompt, cwd, stage):
        calls.append(stage)
        raise ResearchProviderCallError("TIMEOUT: GOOGLE: HARD_TIMEOUT: Exceeded 600s", retryable=True, meta={})
    monkeypatch.setattr(runner, "_invoke_once", once)
    with pytest.raises(ResearchProviderCallError, match="TIMEOUT"):
        runner.invoke("GOOGLE", "p", "/tmp", "BUILDING")
    assert calls == ["BUILDING"]
    calls.clear()
    with pytest.raises(ResearchProviderCallError):
        runner.invoke("GOOGLE", "p", "/tmp", "RESEARCH")      # other stages keep their bounded retry
    assert calls == ["RESEARCH", "RESEARCH"]


# ------------------------------------------------------------------ first-build prompt

def _capture(builder):
    cap = {}
    builder._invoke_and_apply = lambda prompt, order, root, repair: cap.update(prompt=prompt) or "RESULT"
    builder.last_execution_info = {}
    return cap


def _app(tmp_path):
    filler = "// " + "x" * 3000 + "\n"
    # more than NARROW_SCOPE_FILES per allowed pattern, so "App/**" / "Tests/**" do not name every file in them
    extra = {f"App/Screen{i}.swift": f"struct Screen{i} {{}}\n" + filler for i in range(4)}
    extra.update({f"Tests/More{i}Tests.swift": f"final class More{i}Tests {{}}\n" + filler for i in range(4)})
    return git_project(tmp_path / "app", {**extra,
        "App/FriendDetailView.swift": "struct FriendDetailView { let m: FriendLocationModel }\n",
        "App/FriendLocationModel.swift": "final class FriendLocationModel {}\n",
        "App/AlertsTabView.swift": "struct AlertsTabView {}\n" + filler,
        "Tests/OldTests.swift": "final class OldTests {}\n" + filler,
        "App/Assets.xcassets/Contents.json": '{"info": {}}\n',
        "App/icon.png": "not really a png\n",
        "CLAUDE.md": "server 10.0.0.1:22\n",
        "docs/spec/03-detail.md": "spec text\n",
    })


def test_first_build_sends_named_files_and_the_types_they_use(tmp_path):
    proj = _app(tmp_path)
    order = OrderSchema(goal="Finish App/FriendDetailView.swift per docs/spec/03-detail.md",
                        allowed_paths=["App/**", "Tests/**"])
    b = ProposalBuilder(provider="antigravity", executable=sys.executable)
    cap = _capture(b)
    assert b.run(order, proj) == "RESULT"
    shown = b.last_context_files
    assert shown[0] == "App/FriendDetailView.swift"
    assert "App/FriendLocationModel.swift" in shown and "docs/spec/03-detail.md" in shown
    assert "App/AlertsTabView.swift" not in shown and "Tests/OldTests.swift" not in shown
    tail = cap["prompt"].split("OTHER FILES IN SCOPE", 1)[1]
    assert "App/AlertsTabView.swift" in tail and "Tests/OldTests.swift" in tail
    for never in ("Assets.xcassets", "icon.png", "CLAUDE.md", "10.0.0.1"):
        assert never not in cap["prompt"]


def test_first_build_without_named_files_keeps_the_full_prompt(tmp_path):
    proj = _app(tmp_path)
    b = ProposalBuilder(provider="antigravity", executable=sys.executable)
    cap = _capture(b)
    b.run(OrderSchema(goal="improve the app", allowed_paths=["App/**", "Tests/**"]), proj)
    assert "App/AlertsTabView.swift" in b.last_context_files and "OTHER FILES IN SCOPE" not in cap["prompt"]
    assert "CLAUDE.md" not in cap["prompt"]


@pytest.mark.parametrize("rel, skipped", [
    ("App/Assets.xcassets/AppIcon.appiconset/Contents.json", True), ("App/a.png", True), ("CLAUDE.md", True),
    ("web/package-lock.json", True), ("App.xcodeproj/project.pbxproj", True),
    ("App/Localizable.xcstrings", False), ("src/main.swift", False)])
def test_skip_for_prompt(rel, skipped):
    assert ProposalBuilder.skip_for_prompt(rel) is skipped
