import os
import re
import sys
import shlex
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Union, List, Tuple
from core.git_manager import GitManager
from core.process_runner import ProcessTreeRunner, ProcessResult
from core.env_utils import get_sanitized_env

# Shown for a step the project leaves unconfigured on purpose (e.g. tests that only compile in build-for-testing).
SKIPPED_STEP_NOTE = ("Not configured for this project (skipped on purpose, not a failure). "
                     "If verification failed, the cause is in the build output above: fix those compiler errors.")


class Verifier:
    """Orchestrator-driven verification executing build, test, and git checks."""

    DEFAULT_TIMEOUT = 120.0

    @classmethod
    def timeout_from(cls, config: Optional[Dict[str, Any]]) -> float:
        """[fork] Per-command time limit from config `verification.timeout_sec` (per project via `projects:`),
        e.g. an iOS simulator test run that needs a cold boot. Missing/invalid -> 120 s; clamped to 10..1800 s."""
        raw = ((config or {}).get("verification") or {}).get("timeout_sec")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return cls.DEFAULT_TIMEOUT
        if value != value:                       # NaN
            return cls.DEFAULT_TIMEOUT
        return max(10.0, min(1800.0, value))

    @staticmethod
    def _execute_cmd(cmd_str: Optional[Union[str, List[str]]], cwd: Path, timeout: float = 120.0) -> Dict[str, Any]:
        if not cmd_str:
            # Fork (2026-11-07): the old "No command specified" made HEAD/BUILDER/REVIEWER read an intentionally
            # skipped step as a broken verification setup and ignore the real compiler errors in build.output.
            return {"command": None, "executed": False, "passed": True, "output": SKIPPED_STEP_NOTE}
        
        try:
            env = get_sanitized_env()
            # Verify under the user's default Python I/O encoding, not whatever the Director process was launched
            # with: an inherited PYTHONIOENCODING/PYTHONUTF8 once hid a CLI that breaks in a normal cp949 terminal.
            for leaked in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
                env.pop(leaked, None)
            cwd_str = str(cwd)
            current_ppath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{cwd_str}{os.pathsep}{current_ppath}" if current_ppath else cwd_str

            if isinstance(cmd_str, list):
                argv = list(cmd_str)
            else:
                raw_tokens = shlex.split(cmd_str, posix=False)
                argv = []
                for t in raw_tokens:
                    if len(t) >= 2 and ((t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'")):
                        argv.append(t[1:-1])
                    else:
                        argv.append(t)

            if not argv:
                return {"command": cmd_str, "executed": False, "passed": True, "output": "Empty command"}

            # Resolve python to current interpreter if invoked as python/python3
            if argv[0] in ("python", "python3"):
                argv[0] = sys.executable
            else:
                resolved = shutil.which(argv[0])
                if resolved:
                    argv[0] = resolved

            res: ProcessResult = ProcessTreeRunner.run(
                argv=argv,
                cwd=cwd_str,
                env=env,
                timeout=timeout,
                no_output_timeout=None
            )

            if res.timed_out:
                return {
                    "command": cmd_str if isinstance(cmd_str, str) else " ".join(cmd_str),
                    "executed": True,
                    "passed": False,
                    "exit_code": -1,
                    "output": f"Command timed out after {timeout} seconds: {res.timeout_reason}"
                }

            combined_output = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
            passed = (res.exit_code == 0)
            return {
                "command": cmd_str if isinstance(cmd_str, str) else " ".join(cmd_str),
                "executed": True,
                "passed": passed,
                "exit_code": res.exit_code,
                "output": combined_output.strip()
            }
        except Exception as e:
            return {
                "command": cmd_str if isinstance(cmd_str, str) else " ".join(cmd_str),
                "executed": True,
                "passed": False,
                "exit_code": -1,
                "output": f"Execution error: {str(e)}"
            }

    @classmethod
    def verify(
        cls,
        worktree_path: str,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        lint_cmd: Optional[str] = None,
        timeout: float = 120.0,
        git_checks: bool = True
    ) -> Dict[str, Any]:
        wt = Path(worktree_path).resolve()

        # 1. Git checks
        if git_checks:
            git_status = GitManager.get_status(str(wt))
            git_diff = GitManager.get_diff(str(wt))
        else:                                   # apply-verify on the MAIN tree: never touch its index (no intent-to-add)
            git_status, git_diff = "", ""

        # 2. Run build
        build_res = cls._execute_cmd(build_cmd, wt, timeout=timeout)

        # 3. Run test
        test_res = cls._execute_cmd(test_cmd, wt, timeout=timeout)

        # 4. Run lint
        lint_res = cls._execute_cmd(lint_cmd, wt, timeout=timeout)

        # Overall verification pass condition:
        # Build must pass (if executed)
        # Test must pass (if executed)
        # Lint must pass (if executed)
        overall_passed = build_res["passed"] and test_res["passed"] and lint_res["passed"]

        summary_parts = []
        if build_res["executed"]:
            summary_parts.append(f"Build: {'PASS' if build_res['passed'] else 'FAIL'}")
        if test_res["executed"]:
            summary_parts.append(f"Test: {'PASS' if test_res['passed'] else 'FAIL'}")
        if lint_res["executed"]:
            summary_parts.append(f"Lint: {'PASS' if lint_res['passed'] else 'FAIL'}")
        
        summary = " | ".join(summary_parts) if summary_parts else "No build/test commands configured"

        # Parse test metrics (e.g. 4 passed, Ran 4 tests)
        test_counts = cls._parse_test_counts(test_res.get("output", ""))

        build_errors = []
        if build_res.get("executed") and not build_res.get("passed"):
            build_errors = cls.error_digest(build_res.get("output", ""), root=str(wt))

        return {
            "passed": overall_passed,
            "summary": summary,
            "build_errors": build_errors,
            "build": build_res,
            "test": test_res,
            "lint": lint_res,
            "test_counts": test_counts,
            "git_status": git_status,
            "git_diff": git_diff
        }

    # `path:line:col: error: msg` (swift/clang/gcc/rustc-short), `path(line,col): error TS1234: msg` (tsc),
    # bare `error: msg` lines (xcodebuild, cargo) and Python `XxxError:` tracebacks' last line.
    _ERR_PATTERNS = (
        re.compile(r"^(?P<loc>[^\s:][^:]*:\d+(?::\d+)?): (?:fatal )?error: (?P<msg>.+)$"),
        re.compile(r"^(?P<loc>[^\s(][^(]*\(\d+,\d+\)): error (?P<msg>TS\d+: .+)$"),
        re.compile(r"^(?P<loc>)(?:fatal )?error: (?P<msg>.+)$"),
        re.compile(r"^(?P<loc>)(?P<msg>(?:SyntaxError|IndentationError|ModuleNotFoundError|ImportError|NameError): .+)$"),
    )
    # rustc: `error[E0308]: msg` (location on a following ` --> path:line:col` line) or, with
    # --message-format=short, `path:line:col: error[E0308]: msg` on one line.
    _RUST_ERR = re.compile(r"^(?:(?P<loc>[^\s:][^:]*:\d+(?::\d+)?): )?error\[(?P<code>E\d{4})\]: (?P<msg>.+)$")
    _RUST_LOC = re.compile(r"^-->\s*(?P<loc>.+:\d+(?::\d+)?)$")
    _RUST_LABEL = re.compile(r"^\d*\s*\|\s*[\s\-|]*\^+[\^\-]*\s*(?P<label>\S.*)$")
    _RUST_HELP = re.compile(r"^(?:=\s*)?help: (?P<help>.+)$")

    @classmethod
    def _rust_error(cls, lines: List[str], i: int, m) -> Tuple[str, str]:
        """`E0308: mismatched types (expected `i32`, found `&str`; help: ...)` plus its location from the
        ` --> ` line. The detail lines are what tell the Builder how to fix it, so the first primary label and
        the first help line are kept; the scan stops at the next diagnostic."""
        loc, msg = m.group("loc") or "", f"{m.group('code')}: {m.group('msg').strip()}"
        label = help_ = None
        for raw in lines[i + 1:i + 30]:
            line = raw.strip()
            if re.match(r"^(error|warning)(\[|:)", line):
                break
            if not loc:
                lm = cls._RUST_LOC.match(line)
                if lm:
                    loc = lm.group("loc")
                    continue
            if label is None:
                lb = cls._RUST_LABEL.match(line)
                if lb:
                    label = lb.group("label").strip()
                    continue
            if help_ is None:
                hm = cls._RUST_HELP.match(line)
                if hm:
                    help_ = hm.group("help").strip()
        extra = "; ".join(x for x in (label, f"help: {help_}" if help_ else None) if x)
        return loc, msg + (f" ({extra})" if extra else "")

    # Kotlin (Gradle/Android): 2.x `e: file:///abs/Main.kt:3:18 msg`, 1.x `e: /abs/Main.kt: (3, 18): msg`.
    _KOTLIN_ERR = (
        re.compile(r"^e: (?:file://)?(?P<file>.+?\.kts?):(?P<line>\d+):(?P<col>\d+) (?P<msg>.+)$"),
        re.compile(r"^e: (?:file://)?(?P<file>.+?\.kts?): \((?P<line>\d+), (?P<col>\d+)\): (?P<msg>.+)$"),
    )
    # Vite: esbuild transform `abs/src/main.ts:2:8: ERROR: msg`, esbuild CLI `✘ [ERROR] msg` with the location
    # on a later `src/main.ts:3:6:` line, Rollup `Could not resolve "./x" from "src/main.ts"` and the older
    # `[vite]: Rollup failed to resolve import "x" from "/abs/src/main.ts".`
    _ESBUILD_ERR = re.compile(r"^(?P<loc>[^\s:][^:]*:\d+:\d+): ERROR: (?P<msg>.+)$")
    _ESBUILD_CLI = re.compile(r"^✘ \[ERROR\] (?P<msg>.+)$")
    _ESBUILD_LOC = re.compile(r"^(?P<loc>[^\s:][^:]*:\d+:\d+):$")
    _ROLLUP_ERR = (
        re.compile(r'^Could not resolve "(?P<what>[^"]+)" from "(?P<loc>[^"]+)"$'),
        re.compile(r'^\[vite\]: Rollup failed to resolve import "(?P<what>[^"]+)" from "(?P<loc>[^"]+)"'),
    )
    # Next.js `next build`: a `./src/app/page.tsx[:4:18]` header line, then `Type error: msg`,
    # `Module not found: msg`, or an SWC `Error:` block with `x msg` and a caret under a numbered source line.
    _NEXT_HEADER = re.compile(r"^\./(?P<file>[^\s:]+)(?::(?P<line>\d+):(?P<col>\d+))?$")
    _NEXT_MSG = re.compile(r"^(?P<msg>(?:Type error|Module not found|Syntax error): .+)$")
    _ANSI = re.compile(r"\x1b\[[0-9;]*m")

    @classmethod
    def _toolchain_error(cls, lines: List[str], i: int, line: str) -> Optional[Tuple[str, str]]:
        """(location, message) for the Kotlin, Vite/esbuild/Rollup and Next.js formats, or None."""
        for pat in cls._KOTLIN_ERR:
            m = pat.match(line)
            if m:
                path = m.group("file")
                if re.match(r"^/[A-Za-z]:/", path):        # file:///D:/work/... on Windows
                    path = path[1:]
                return f"{path}:{m.group('line')}:{m.group('col')}", m.group("msg").strip()
        m = cls._ESBUILD_ERR.match(line)
        if m:
            return m.group("loc"), m.group("msg").strip()
        m = cls._ESBUILD_CLI.match(line)
        if m:
            loc = ""
            for raw in lines[i + 1:i + 6]:
                lm = cls._ESBUILD_LOC.match(raw.strip())
                if lm:
                    loc = lm.group("loc")
                    break
            return loc, m.group("msg").strip()
        for pat in cls._ROLLUP_ERR:
            m = pat.match(line)
            if m:
                return m.group("loc"), f'Could not resolve "{m.group("what")}"'
        m = cls._NEXT_HEADER.match(line)
        if m:
            loc = m.group("file") + (f":{m.group('line')}:{m.group('col')}" if m.group("line") else "")
            follow = [raw.strip() for raw in lines[i + 1:i + 25]]
            first = next((f for f in follow if f), "")
            nm = cls._NEXT_MSG.match(first)
            if nm:
                return loc, nm.group("msg").strip()
            if first == "Error:":
                # SWC: `x Expression expected`, then ` 2 |   const a = ;` and `:   ^` under it
                msg, src_line = None, None
                for f in follow[1:]:
                    if msg is None and f.startswith("x "):
                        msg = f[2:].strip()
                    num = re.match(r"^(\d+) \|", f)
                    if num:
                        src_line = num.group(1)
                    elif msg and src_line and re.match(r"^:\s*\^", f):
                        loc = f"{m.group('file')}:{src_line}"
                        break
                    if f.startswith("Caused by:") or f.startswith("Import trace"):
                        break
                if msg:
                    return loc, f"Syntax error: {msg}"
        return None

    @classmethod
    def error_digest(cls, output: str, root: Optional[str] = None, limit: int = 40) -> List[str]:
        """The compiler's own error lines, de-duplicated, with the worktree prefix removed.

        A long build log (xcodebuild prints every compiler invocation) hides the few lines that matter; a tail cut
        keeps the command lines and loses the errors, so the Builder/Reviewer/HEAD never see why the build failed."""
        if not output:
            return []
        prefixes = []
        if root:
            r = str(root).rstrip("/\\")
            prefixes = [r + "/", r + "\\", r.replace("\\", "/") + "/", str(Path(r).resolve()) + "/"]
        seen, out = set(), []
        lines = cls._ANSI.sub("", output).splitlines()     # next build colours its output
        for idx, raw in enumerate(lines):
            line = raw.strip()
            low = line.lower()
            if "error" not in low and not (line.startswith(("e: ", "./", "[vite]")) or "could not resolve" in low):
                continue
            rust = cls._RUST_ERR.match(line)
            other = None if rust else cls._toolchain_error(lines, idx, line)
            for pat in ((cls._RUST_ERR,) if rust else (None,) if other else cls._ERR_PATTERNS):
                m = pat.match(line) if pat is not None else None
                if pat is not None and not m:
                    continue
                if rust:
                    loc, msg = cls._rust_error(lines, idx, m)
                elif other:
                    loc, msg = other
                else:
                    loc, msg = m.group("loc"), m.group("msg").strip()
                for p in prefixes:
                    if loc.startswith(p):
                        loc = loc[len(p):]
                        break
                item = f"{loc}: error: {msg}" if loc else f"error: {msg}"
                if item not in seen:
                    seen.add(item)
                    out.append(item[:400])
                break
            if len(out) >= limit:
                break
        # file-located errors first: they are the actionable ones (bare `error:` lines are often summaries)
        return sorted(out, key=lambda s: s.startswith("error:"))

    @staticmethod
    def _parse_test_counts(output: str) -> Dict[str, int]:
        import re
        counts = {"passed": 0, "failed": 0, "total": 0}
        if not output:
            return counts

        # Pytest pattern: '12 passed, 1 failed' or '4 passed in 0.02s'
        pass_match = re.search(r"(\d+)\s+passed", output)
        if pass_match:
            counts["passed"] = int(pass_match.group(1))

        fail_match = re.search(r"(\d+)\s+failed", output)
        if fail_match:
            counts["failed"] = int(fail_match.group(1))

        # Unittest pattern: 'Ran 4 tests in 0.001s'
        ran_match = re.search(r"Ran\s+(\d+)\s+tests", output)
        if ran_match:
            counts["total"] = int(ran_match.group(1))
            if "OK" in output:
                counts["passed"] = counts["total"]

        # Swift (2026-11-13): XCTest "Executed 12 tests, with 1 failure" (last line = overall) and
        # Swift Testing "Test run with 169 tests in 25 suites passed" / "... failed after 0.1 seconds with 2 issues".
        xc = re.findall(r"Executed\s+(\d+)\s+tests?,\s+with\s+(\d+)\s+failures?", output)
        st_ok = re.findall(r"Test run with\s+(\d+)\s+tests?(?:\s+in\s+\d+\s+suites?)?\s+passed", output)
        st_bad = re.findall(r"Test run with\s+(\d+)\s+tests?(?:\s+in\s+\d+\s+suites?)?\s+failed", output)
        if xc or st_ok or st_bad:
            total = failed = 0
            if xc:
                n, f = (int(x) for x in xc[-1])
                total, failed = total + n, failed + f
            if st_ok:
                total += int(st_ok[-1])
            elif st_bad:
                total += int(st_bad[-1])
                failed += max(1, len([l for l in re.findall(r"^.*Test .+ failed after.*$", output, re.M) if "Test run with" not in l]))
            counts = {"passed": total - failed, "failed": failed, "total": total}

        if counts["total"] == 0:
            counts["total"] = counts["passed"] + counts["failed"]

        return counts
