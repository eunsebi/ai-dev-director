"""[fork] Claude HEAD may be given read-only shared-memory MCP tools (config head.allowed_tools).
They are offered only on chat/ORDER planning calls; judging calls stay tool-less, and the default is unchanged."""
import json
from types import SimpleNamespace

import pytest

from agents import claude_head as ch
from agents.claude_head import ClaudeHead

TOOLS = ["mcp__claude-memory__memory_index", "mcp__claude-memory__memory_read", "mcp__claude-memory__memory_search"]


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen = []
    exe = tmp_path / "claude"; exe.write_text(""); exe.chmod(0o755)

    def fake_run(argv, cwd, env, timeout, no_output_timeout, heartbeat_interval, stdin_data):
        seen.append({"argv": argv, "prompt": stdin_data})
        body = {"type": "QUESTION", "message": "ok"}
        return SimpleNamespace(stdout=json.dumps({"result": json.dumps(body)}), stderr="", exit_code=0,
                               duration_sec=0.1, timed_out=False, timeout_reason=None)
    monkeypatch.setattr(ch.ProcessTreeRunner, "run", staticmethod(fake_run))
    monkeypatch.setattr(ClaudeHead, "get_version", lambda self: "test")
    return seen, str(exe)


def test_default_head_passes_no_tools(calls):
    seen, exe = calls
    ClaudeHead(executable=exe).chat("hi", {"project_path": None}, [])
    assert "--allowedTools" not in seen[0]["argv"] and "SHARED MEMORY" not in seen[0]["prompt"]


def test_chat_gets_memory_tools_and_hint(calls):
    seen, exe = calls
    ClaudeHead(executable=exe, allowed_tools=TOOLS).chat("hi", {"project_path": None}, [])
    argv = seen[0]["argv"]
    assert argv[argv.index("--allowedTools") + 1] == ",".join(TOOLS)
    assert seen[0]["prompt"].startswith("SHARED MEMORY")


def test_judging_calls_stay_tool_less(calls):
    seen, exe = calls
    ClaudeHead(executable=exe, allowed_tools=TOOLS)._invoke_cli("judge", cwd=None)
    assert "--allowedTools" not in seen[0]["argv"]


def test_orchestrator_reads_allowed_tools_from_config():
    from core.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o.config = {"head": {"provider": "claude", "allowed_tools": TOOLS}}
    o.timeouts = {}
    assert o._init_head().allowed_tools == TOOLS
