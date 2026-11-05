"""Native folder dialog for the 8081 console. A browser never reveals a folder's absolute path, so the local
companion server runs this helper and reads the choice from stdout: one JSON line {"path": "..."} ("" = cancelled).
Windows/Linux use tkinter; macOS uses AppleScript `choose folder` (a Homebrew/python.org venv often has no _tkinter)."""
import json
import subprocess
import sys


def _applescript_string(text: str) -> str:
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def pick_macos(initial: str) -> str:
    prompt = _applescript_string("AI DEV DIRECTOR - 작업할 폴더 선택")
    where = f" default location (POSIX file {_applescript_string(initial)})" if initial else ""
    script = f"activate\nPOSIX path of (choose folder with prompt {prompt}{where})"
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if out.returncode != 0:
        if "-128" in (out.stderr or ""):          # the user pressed Cancel
            return ""
        raise RuntimeError(out.stderr.strip() or "osascript failed")
    chosen = out.stdout.strip()
    return chosen.rstrip("/") if len(chosen) > 1 else chosen


def pick_tk(initial: str) -> str:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)          # the dialog must not open behind the browser
    root.update()
    chosen = filedialog.askdirectory(parent=root, initialdir=initial or None, mustexist=True,
                                     title="AI DEV DIRECTOR - 작업할 폴더 선택")
    root.destroy()
    return chosen or ""


def main() -> int:
    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    chosen = pick_macos(initial) if sys.platform == "darwin" else pick_tk(initial)
    sys.stdout.buffer.write(json.dumps({"path": chosen}).encode("ascii"))   # ensure_ascii: safe on any codepage
    return 0


if __name__ == "__main__":
    sys.exit(main())
