"""String Catalog check for Xcode projects (fork 2026-11-09, used by core/apply_pipeline.py).

A command-line build (Director's build-for-testing) extracts every localizable literal into `*.stringsdata` files but,
unlike a build inside the Xcode app, never touches `Localizable.xcstrings`. Xcode only syncs the catalog on the next
⌘R/⌘U, which then shows up as a surprise diff (new keys, keys marked "stale"). This module compares the two before the
deliverable is applied, so the operator sees it up front:

- missing:  keys the code now uses that the catalog does not have yet (Xcode adds them on the next IDE build)
- stale:    catalog keys already marked stale, plus extracted keys no code uses any more (Xcode marks them stale)
- ternary:  a Korean literal inside a ternary on a changed Swift line (`cond ? "가" : "나"`); older Xcode versions
            extracted only one side, so the project rule is to write if/else instead

Config (projects.<path>.apply_pipeline.string_catalog):
    catalog: "Eodiya/Resources/Localizable.xcstrings"    # relative to the project / worktree
    derived_data: "/Users/me/Library/Caches/ai-dev-director/Eodiya"   # the -derivedDataPath of the build
    table: "Localizable"                                   # default
    build_if_missing: true                                 # no stringsdata for this tree yet -> run the project's build
    fail_on: []                                            # any of "missing", "stale", "ternary"; default report only
"""
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_TERNARY = re.compile(r'\?\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"')
_HANGUL = re.compile(r"[가-힣]")


def find_stringsdata(derived_data: str, root: str) -> List[Path]:
    """stringsdata files whose source file lives under `root` (one derivedDataPath is shared by the project and every
    worktree, so the `source` field tells which tree the last build of that file came from)."""
    base = Path(derived_data).expanduser()
    if not base.is_dir():
        return []
    prefix = str(Path(root).resolve()).rstrip("/") + "/"
    found = []
    for f in base.glob("Build/Intermediates.noindex/**/*.stringsdata"):
        try:
            src = str(json.loads(f.read_text(encoding="utf-8")).get("source") or "")
        except (OSError, ValueError):
            continue
        if src.startswith(prefix) and Path(src).exists():
            found.append(f)
    return found


def extracted_keys(files: Iterable[Path], table: str = "Localizable") -> Dict[str, List[str]]:
    """key -> source locations ("rel/path.swift:line") from stringsdata files."""
    keys: Dict[str, List[str]] = {}
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        src = Path(str(data.get("source") or "")).name
        for e in (data.get("tables") or {}).get(table) or []:
            key = e.get("key")
            if key is None:
                continue
            line = (e.get("location") or {}).get("startingLine")
            keys.setdefault(key, []).append(f"{src}:{line}" if line else src)
    return keys


def compare(catalog_path: Path, keys: Dict[str, List[str]]) -> Dict[str, Any]:
    data = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    strings = data.get("strings") or {}
    missing = sorted(k for k in keys if k not in strings)
    marked = sorted(k for k, v in strings.items() if (v or {}).get("extractionState") == "stale")
    # keys Xcode extracted on its own (no extractionState, or "extracted_with_value") that no code uses any more;
    # "manual" entries are the user's own and never go stale
    unused = sorted(k for k, v in strings.items() if k not in keys
                    and (v or {}).get("extractionState") in (None, "extracted_with_value"))
    return {"missing": missing, "stale_marked": marked, "unused": unused, "catalog_keys": len(strings),
            "code_keys": len(keys)}


def ternary_lines(root: str, rel_paths: Iterable[str], origin_ref: Optional[str] = None) -> List[str]:
    """Changed Swift lines with a Korean literal on either side of a ternary. With origin_ref only the lines added
    since that commit count (a new file counts whole)."""
    hits: List[str] = []
    for rel in rel_paths:
        if not rel.endswith(".swift"):
            continue
        path = Path(root) / rel
        if not path.is_file():
            continue
        added: Optional[set] = None
        if origin_ref:
            r = subprocess.run(["git", "diff", "-U0", "--no-color", origin_ref, "--", rel], cwd=root,
                               capture_output=True, text=True)
            if r.returncode == 0 and r.stdout:
                added = set()
                for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", r.stdout, re.M):
                    start, count = int(m.group(1)), int(m.group(2) or 1)
                    added.update(range(start, start + count))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for no, line in enumerate(lines, 1):
            if added is not None and no not in added:
                continue
            if line.lstrip().startswith("//"):
                continue
            for m in _TERNARY.finditer(line):
                if _HANGUL.search(m.group(1) + m.group(2)):
                    hits.append(f"{rel}:{no}")
                    break
    return hits


def summarize(report: Dict[str, Any], limit: int = 6) -> str:
    def few(items):
        items = list(items)
        return ", ".join(f"'{i[:30]}'" for i in items[:limit]) + (f" 외 {len(items) - limit}개" if len(items) > limit else "")
    parts = [f"코드 키 {report.get('code_keys', 0)}개 · 카탈로그 {report.get('catalog_keys', 0)}개"]
    if report.get("missing"):
        parts.append(f"카탈로그에 없는 키 {len(report['missing'])}개(Xcode ⌘R/⌘U 때 추가됨): {few(report['missing'])}")
    stale = list(report.get("stale_marked") or []) + list(report.get("unused") or [])
    if stale:
        parts.append(f"stale {len(stale)}개(코드에서 안 씀): {few(stale)}")
    if report.get("ternary"):
        parts.append(f"삼항식 안 한국어 문구 {len(report['ternary'])}곳(if/else 권장): {', '.join(report['ternary'][:limit])}")
    return "\n".join(parts)
