"""Lean Claude Code CLI options for Director's non-interactive calls (fork 2026-11-08).

A plain `claude --print` loads everything an interactive session has: every MCP server the user configured (tool
definitions for hundreds of tools), skills, hooks and settings. Measured on the Mac (Haiku, one-line prompt): about
38k tokens per call with the defaults, about 4k with these options. With tools available, a HEAD judging call in the
project folder also followed CLAUDE.md "read memory / explore first" rules and took several turns (68k chars of
prompt -> 704k tokens).

`lean_args(allowed_tools)` returns the options and, when the allowed tools name MCP servers (`mcp__<server>__<tool>`),
a temporary `--mcp-config` file with only those servers, copied from the user's ~/.claude.json. The file can hold
secret headers: it is created 0600 and removed when the call ends.

Turn off with `claude_cli.lean: false` in config.yaml or DIRECTOR_CLAUDE_LEAN=0.
"""
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

BASE_ARGS = ["--strict-mcp-config", "--disable-slash-commands", "--setting-sources", ""]

_enabled_override: Optional[bool] = None


def configure(config: Optional[dict]) -> None:
    """Called once with the Director config (claude_cli.lean, default true)."""
    global _enabled_override
    value = ((config or {}).get("claude_cli") or {}).get("lean")
    _enabled_override = None if value is None else bool(value)


def enabled() -> bool:
    if os.environ.get("DIRECTOR_CLAUDE_LEAN", "").strip() == "0":
        return False
    return True if _enabled_override is None else _enabled_override


def mcp_servers(allowed_tools: Optional[Iterable[str]]) -> List[str]:
    names = []
    for tool in allowed_tools or []:
        parts = str(tool).split("__")
        if len(parts) >= 3 and parts[0] == "mcp" and parts[1] and parts[1] not in names:
            names.append(parts[1])
    return names


def _user_config_path() -> Path:
    return Path(os.environ.get("DIRECTOR_CLAUDE_JSON") or (Path.home() / ".claude.json"))


def server_configs(names: Iterable[str]) -> Dict[str, dict]:
    try:
        data = json.loads(_user_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") or {}
    return {n: servers[n] for n in names if isinstance(servers.get(n), dict)}



def static_args(builtin_tools: str = "") -> List[str]:
    """For a call that needs no MCP server (nothing to clean up)."""
    return (list(BASE_ARGS) + ["--tools", builtin_tools]) if enabled() else []


@contextlib.contextmanager
def lean_args(allowed_tools: Optional[Iterable[str]] = None, builtin_tools: str = "") -> Iterator[List[str]]:
    """builtin_tools: "" = no built-in tools (Read/Bash/...), or e.g. "Read,Grep,Glob" for a read-only reviewer."""
    if not enabled():
        yield []
        return
    args = list(BASE_ARGS) + ["--tools", builtin_tools]
    path = None
    configs = server_configs(mcp_servers(allowed_tools))
    if configs:
        fd, path = tempfile.mkstemp(prefix="director-mcp-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": configs}, fh)
        except Exception:
            os.unlink(path)
            raise
        args += ["--mcp-config", path]
    try:
        yield args
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
