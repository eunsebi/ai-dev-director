#!/usr/bin/env python3
"""second-opinion: 대화형 Claude Code → Codex·Antigravity에 「두 번째 의견」만 묻기 (fork 2026-11-20).

Director 미션(ORDER → BUILDER → 검증 → 리뷰)을 돌릴 만큼은 아닌 짧은 질문 — 설계 선택, 버그 원인 추정, 지금 고친 diff
검토 — 을 다른 AI에게 읽기 전용으로 물어보고 답만 받아 온다. 새 실행기를 만들지 않고 TRIAD 연구 모드의
`TriadResearchRunner`를 그대로 쓴다:
  - 구독 로그인만 사용(*_API_KEY 제거), shell=False 인수 배열, 프로세스 트리째 시간 제한(ProcessTreeRunner)
  - 질문마다 일회용 작업본(프로젝트 HEAD의 git worktree, 또는 빈 저장소)에서 실행, 호출 전후 git 상태가 달라지면 실패
  - 일시적 실패(시간 초과·빈 응답)만 1번 재시도, 한도·로그인 오류는 재시도하지 않음
  - 호출은 작업 장부(data/director.db ai_usage)에 남아 콘솔 「AI별 작업량」에 보인다
지휘자(Claude)는 직원 목록에 없다. 원본 프로젝트는 읽지도 쓰지도 않고, 작업본은 끝나면 지운다.

  second-opinion check                                   CLI 경로·버전, 남은 한도(모델 호출 없음)
  second-opinion ask codex|agy|both [--project <별칭|경로>] [--file 질문.md] [--with-diff]
                                    [--timeout 초] [--force]
      질문은 --file 또는 표준입력. --project가 없으면 빈 작업본(파일 없음)에서 질문만 전달한다.
      --with-diff: 아직 커밋하지 않은 변경(git diff HEAD, 비밀 의심 파일 제외, 40k자 상한)을 질문에 붙인다.
      --force: 한도 문턱(config limit_guard 값)을 넘었어도 묻는다.

기록: runs/second-opinion/SO-<시각>-<해시>/report.md, result.json (runs/는 git에 올라가지 않음).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path


def _director_home() -> Path:
    here = Path(__file__).resolve().parent.parent
    if (here / "core" / "research_runner.py").is_file():
        return here
    return Path(os.environ.get("DIRECTOR_HOME") or Path.home() / "work" / "ai-dev-director")


DIRECTOR = _director_home()
PROJECTS = Path(os.environ.get("DIRECTOR_INTAKE_PROJECTS") or Path.home() / ".config" / "director-intake" / "projects.json")

try:
    import psutil  # noqa: F401  (Director 의존성; 없으면 Director venv로 다시 실행)
except ImportError:
    venv_python = DIRECTOR / ".venv" / "bin" / "python"
    if venv_python.is_file() and Path(sys.prefix).resolve() != (DIRECTOR / ".venv").resolve():
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    print(json.dumps({"ok": False, "error": f"Director 가상환경을 찾지 못했습니다: {venv_python}"}, ensure_ascii=False))
    sys.exit(3)

sys.path.insert(0, str(DIRECTOR))

from core import limit_guard  # noqa: E402
from core.env_utils import probe_version, resolve_cli, run_capture  # noqa: E402
from core.provider_manager import ErrorCategory  # noqa: E402
from core.research_runner import (  # noqa: E402
    ResearchProviderCallError, TriadResearchRunner, cleanup_research_workspaces, create_research_workspaces,
)

WORKERS = {"codex": ("OPENAI", "Codex", "codex"), "agy": ("GOOGLE", "Antigravity", "agy")}
MAX_QUESTION = 20000
MAX_DIFF = 40000
DIFF_EXCLUDES = ["**/.env*", "**/*.pem", "**/*.key", "**/*.p12", "**/*.token", "**/*secret*", "**/*credential*",
                 "**/GoogleService-Info.plist", "**/CLAUDE.md", "**/config.yaml"]
KIND_KO = {
    "QUOTA": "한도 소진", "AUTH": "로그인 필요", "TIMEOUT": "시간 초과", "PROVIDER_UNAVAILABLE": "서비스 연결 안 됨",
    "CONFIG_ERROR": "CLI 없음·설정 오류", "EXECUTION_ERROR": "실행 오류", "UNKNOWN": "알 수 없는 오류",
    "READ_ONLY_VIOLATION": "작업본을 바꾸려 함(답 버림)", "EMPTY": "빈 응답",
}
NEXT_STEP = {
    "QUOTA": "한도가 풀릴 때까지 다른 직원만 쓰거나, Claude 혼자 판단해요.",
    "AUTH": "맥 터미널에서 해당 CLI 로그인을 다시 해 주세요(codex login / agy 로그인).",
    "TIMEOUT": "질문을 더 짧게 줄이거나 --timeout을 늘려 다시 물어요.",
    "PROVIDER_UNAVAILABLE": "서비스 상태를 확인하고 잠시 뒤 다시 물어요.",
    "CONFIG_ERROR": "CLI 설치·PATH를 확인해 주세요(second-opinion check).",
    "READ_ONLY_VIOLATION": "읽기 전용 질문인데 파일을 바꾸려 했어요. 질문에서 「수정」 요청을 빼고 다시 물어요.",
}
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|[A-Za-z0-9_\-]{40,})")


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def redact(text: str, limit: int = 300) -> str:
    return _SECRET_RE.sub("[가림]", str(text or "")).strip()[:limit]


def load_config() -> dict:
    try:
        import yaml
        return yaml.safe_load((DIRECTOR / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def resolve_project(name):
    try:
        raw = json.loads(PROJECTS.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        raw = {}
    entry = raw.get(name) if isinstance(raw, dict) else None
    path = Path(entry["path"] if isinstance(entry, dict) and entry.get("path") else name).expanduser()
    if not path.is_dir():
        out({"ok": False, "error": f"모르는 프로젝트입니다: {name}", "projects": sorted(raw) if isinstance(raw, dict) else []}, 2)
    path = path.resolve()
    if path == Path.home().resolve():
        out({"ok": False, "error": "홈 폴더 전체는 넘기지 않아요. 프로젝트 폴더를 지정해 주세요."}, 2)
    head = run_capture(["git", "rev-parse", "--show-toplevel"], cwd=path, timeout=15)
    if head.returncode != 0:
        out({"ok": False, "error": f"git 저장소가 아니에요: {path}. --project 없이 빈 작업본으로 물어볼 수 있어요."}, 2)
    top = Path(head.stdout.strip()).resolve()
    if run_capture(["git", "rev-parse", "--verify", "HEAD"], cwd=top, timeout=15).returncode != 0:
        out({"ok": False, "error": f"커밋이 하나도 없는 저장소예요: {top}"}, 2)
    return top


def working_diff(project: Path) -> str:
    argv = ["git", "-c", "core.quotepath=false", "diff", "HEAD", "--", "."]
    argv += [f":(exclude,glob){p}" for p in DIFF_EXCLUDES]
    res = run_capture(argv, cwd=project, timeout=30)
    diff = res.stdout if res.returncode == 0 else ""
    if len(diff) > MAX_DIFF:
        diff = diff[:MAX_DIFF] + f"\n… (diff {len(res.stdout)}자 중 {MAX_DIFF}자까지만 붙임)"
    return diff


def build_prompt(question: str, diff: str, has_project: bool) -> str:
    where = ("현재 폴더는 그 프로젝트의 마지막 커밋(HEAD)을 복사한 일회용 작업본이에요. 필요하면 파일을 읽어도 되지만, "
             "아직 커밋하지 않은 변경은 이 폴더에 없어요." if has_project else
             "현재 폴더는 비어 있어요. 질문에 적힌 내용만으로 판단해 주세요.")
    parts = [
        "너는 다른 AI(Claude Code)가 작업 중에 「두 번째 의견」을 구하려고 부른 독립 검토자야.",
        where,
        "규칙: 파일을 만들거나 고치거나 지우지 말고, 명령으로 무언가를 설치·실행하지 마. 외부로 아무것도 보내지 마. 읽고 생각해서 답만 해.",
        "Claude의 의견에 맞춰 주지 말고, 동의하지 않으면 분명히 말해. 확실하지 않은 것은 「추정」이라고 표시해.",
        "한국어로, 아래 형식으로 짧게(40줄 이내) 답해:\n## 결론\n## 근거\n## 놓친 위험\n## 대안(있으면)",
        "----- 질문 -----",
        question.strip(),
    ]
    if diff.strip():
        parts += ["----- 아직 커밋하지 않은 변경 (git diff HEAD, 비밀 의심 파일 제외) -----", diff]
    return "\n\n".join(parts)


def limit_hits(selected, force: bool):
    """선택한 직원의 한도가 문턱(config limit_guard의 값, enabled와 무관)을 넘었으면 {key: 설명}. 못 읽으면 막지 않는다."""
    if force:
        return {}
    opts = limit_guard.settings(load_config())
    hits = {}
    try:
        from core import subscription_limits as sl
        for key in selected:
            domain = WORKERS[key][0]
            entry = sl.codex_limits() if domain == "OPENAI" else sl.antigravity_limits(0)
            hit = limit_guard.over_limit({"providers": {domain: entry or {}}}, [domain], opts)
            if hit:
                hits[key] = limit_guard.describe(hit)
    except Exception:
        return {}
    return hits


def blank_workspaces(root: Path, domains):
    created = {}
    for domain in domains:
        path = root / domain.lower()
        path.mkdir(parents=True, exist_ok=True)
        for argv in (["git", "init", "-q"],
                     ["git", "-c", "user.name=second-opinion", "-c", "user.email=second-opinion@localhost",
                      "commit", "-q", "--allow-empty", "-m", "empty workspace"]):
            res = run_capture(argv, cwd=path, timeout=30)
            if res.returncode != 0:
                raise RuntimeError(f"빈 작업본을 만들지 못했어요: {redact(res.stderr)}")
        created[domain] = str(path)
    return created


def classify(message: str) -> str:
    if message.startswith("RESEARCH_READ_ONLY_VIOLATION"):
        return "READ_ONLY_VIOLATION"
    if message.startswith("GOOGLE_STREAM_EMPTY_RESPONSE"):
        return "EMPTY"
    if message.startswith("TIMEOUT:"):
        return "TIMEOUT"
    if message.startswith("CONFIG_ERROR"):
        return "CONFIG_ERROR"
    return ErrorCategory.classify(message).value


def ask_one(runner, key, prompt, cwd, results):
    domain, name, _ = WORKERS[key]
    try:
        res = runner.invoke(domain, prompt, cwd, "SECOND_OPINION")
        answer = (res.get("output") or "").strip()
        if not answer:
            raise ResearchProviderCallError("GOOGLE_STREAM_EMPTY_RESPONSE" if domain == "GOOGLE" else "EMPTY: no output")
        results[key] = {"worker": name, "ok": True, "answer": answer, "duration_sec": res.get("duration_sec"),
                        "attempts": len(res.get("attempts") or []) + 1, "version": res.get("version")}
    except ResearchProviderCallError as exc:
        message = str(exc)
        kind = "EMPTY" if message.startswith("EMPTY") else classify(message)
        results[key] = {"worker": name, "ok": False, "error_kind": kind, "error_ko": KIND_KO.get(kind, kind),
                        "error": redact(message), "next": NEXT_STEP.get(kind),
                        "attempts": len(getattr(exc, "attempts", None) or []) or 1}
    except Exception as exc:  # 실행기 바깥 오류도 답 없이 실패로 기록
        results[key] = {"worker": name, "ok": False, "error_kind": "UNKNOWN", "error_ko": KIND_KO["UNKNOWN"],
                        "error": redact(f"{type(exc).__name__}: {exc}")}


def write_report(run_dir: Path, question: str, meta: dict, results: dict) -> Path:
    lines = [f"# 두 번째 의견 · {meta['started_at']}", "",
             f"- 작업본: {meta['workspace']}", f"- 프로젝트: {meta.get('project') or '없음(빈 작업본)'}",
             f"- 커밋 안 된 변경 첨부: {'예' if meta.get('with_diff') else '아니오'}", "", "## 질문", "", question.strip(), ""]
    for key, r in results.items():
        lines += [f"## {r['worker']}", ""]
        if r["ok"]:
            lines += [f"_{r.get('duration_sec')}초 · 시도 {r.get('attempts')}번_", "", r["answer"], ""]
        else:
            lines += [f"**실패 — {r['error_ko']}**: {r['error']}", ""]
            if r.get("next"):
                lines += [f"다음: {r['next']}", ""]
    path = run_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps({**meta, "results": results}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    return path


def cmd_ask(args):
    selected = ["codex", "agy"] if args.worker == "both" else [args.worker]
    question = (Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()).strip()
    if not 10 <= len(question) <= MAX_QUESTION:
        out({"ok": False, "error": f"질문은 10~{MAX_QUESTION}자여야 해요", "length": len(question)}, 2)
    if args.with_diff and not args.project:
        out({"ok": False, "error": "--with-diff는 --project와 같이 써야 해요"}, 2)
    project = resolve_project(args.project) if args.project else None

    blocked = limit_hits(selected, args.force)
    selected = [k for k in selected if k not in blocked]
    if not selected:
        out({"ok": False, "status": "limit", "limits": blocked,
             "say": "고른 직원이 모두 한도 문턱을 넘었어요. 풀린 뒤 묻거나 --force로 물어볼 수 있어요."}, 1)

    diff = working_diff(project) if project and args.with_diff else ""
    prompt = build_prompt(question, diff, project is not None)
    started = datetime.now()
    digest = hashlib.sha256(f"{project}\n{question}".encode("utf-8")).hexdigest()[:8]
    run_dir = DIRECTOR / "runs" / "second-opinion" / f"SO-{started:%Y%m%d-%H%M%S}-{digest}"
    ws_root = run_dir / "workspaces"
    domains = [WORKERS[k][0] for k in selected]
    workspaces = {}
    results = {}
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        workspaces = (create_research_workspaces(str(project), ws_root, domains) if project
                      else blank_workspaces(ws_root, domains))
        runner = TriadResearchRunner(timeout=float(args.timeout), no_output_timeout=min(180.0, float(args.timeout)))
        threads = [threading.Thread(target=ask_one, args=(runner, k, prompt, workspaces[WORKERS[k][0]], results))
                   for k in selected]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except Exception as exc:
        out({"ok": False, "error": redact(f"작업본 준비 실패: {exc}", 500)}, 1)
    finally:
        if project and workspaces:
            cleanup_research_workspaces(str(project), workspaces)
        shutil.rmtree(ws_root, ignore_errors=True)

    for key, why in blocked.items():
        results[key] = {"worker": WORKERS[key][1], "ok": False, "error_kind": "LIMIT_GUARD", "error_ko": "한도 문턱",
                        "error": why, "next": "풀린 뒤 묻거나 --force로 물어볼 수 있어요."}
    meta = {"started_at": started.isoformat(timespec="seconds"), "project": str(project) if project else None,
            "workspace": "프로젝트 HEAD 일회용 worktree" if project else "빈 작업본",
            "with_diff": bool(diff.strip()), "diff_chars": len(diff)}
    report = write_report(run_dir, question, meta, results)
    ok_any = any(r["ok"] for r in results.values())
    out({"ok": ok_any, "report": str(report), **meta, "results": results,
         "note": "커밋 안 된 변경은 --with-diff로만 전달돼요." if project and not args.with_diff else None},
        0 if ok_any else 1)


def cmd_check(_args):
    blocked = limit_hits(list(WORKERS), False)
    info = {}
    for key, (domain, name, exe_name) in WORKERS.items():
        exe = resolve_cli(exe_name)
        info[key] = {"worker": name, "path": exe, "version": probe_version(exe) if exe else None,
                     "installed": bool(exe), "limit": blocked.get(key) or "문턱 아래(또는 읽지 못함)"}
    ok = all(v["installed"] for v in info.values())
    out({"ok": ok, "workers": info,
         "note": "설치·한도만 확인했어요. 로그인과 실제 응답은 ask로 한 문장 시험을 해야 알 수 있어요."}, 0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(prog="second-opinion")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("check")
    a = sub.add_parser("ask")
    a.add_argument("worker", choices=["codex", "agy", "both"])
    a.add_argument("--project")
    a.add_argument("--file")
    a.add_argument("--with-diff", action="store_true")
    a.add_argument("--timeout", type=int, default=300)
    a.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.cmd == "check":
        cmd_check(args)
    if args.cmd == "ask":
        if not 30 <= args.timeout <= 900:
            out({"ok": False, "error": "--timeout은 30~900초여야 해요"}, 2)
        cmd_ask(args)
    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
