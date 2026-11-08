#!/bin/bash
# macOS: Director 서버를 로그인 때 자동 실행하고, 메뉴 막대 앱(켜기·끄기·다시 시작·로그)을 설치한다.
#   tools/macos/install.sh            설치(다시 실행하면 갱신)
#   tools/macos/install.sh uninstall  둘 다 내리고 지운다
# 서버가 claude/codex/agy를 찾을 수 있게, 실행한 셸의 PATH를 LaunchAgent에 그대로 적는다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UID_N="$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
SERVER_LABEL="local.ai-dev-director.server"
MENU_LABEL="local.ai-dev-director.menu"
APP="$HOME/Applications/Director 메뉴.app"
DEV="${DEVELOPER_DIR:-$(xcode-select -p)}"

unload() { launchctl bootout "gui/$UID_N/$1" 2>/dev/null || true; }

if [ "${1:-}" = "uninstall" ]; then
  unload "$MENU_LABEL"; unload "$SERVER_LABEL"
  rm -f "$AGENTS/$SERVER_LABEL.plist" "$AGENTS/$MENU_LABEL.plist"
  rm -rf "$APP"
  echo "removed"; exit 0
fi

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

# 1. 메뉴 막대 앱 빌드
mkdir -p "$APP/Contents/MacOS" "$AGENTS" "$ROOT/logs"
DEVELOPER_DIR="$DEV" xcrun swiftc -O -target "$(uname -m)-apple-macos13" \
  -o "$APP/Contents/MacOS/DirectorMenu" "$ROOT/tools/macos/DirectorMenu.swift"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>$MENU_LABEL</string>
  <key>CFBundleName</key><string>Director 메뉴</string>
  <key>CFBundleExecutable</key><string>DirectorMenu</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict></plist>
EOF
codesign --force --sign - "$APP" >/dev/null

# 2. 서버 LaunchAgent: 로그인 때 실행. 끄기는 메뉴에서(KeepAlive 없음 → 끈 채로 남는다)
cat > "$AGENTS/$SERVER_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SERVER_LABEL</string>
  <key>ProgramArguments</key><array><string>$(xml "$ROOT/tools/macos/run_companion.sh")</string></array>
  <key>WorkingDirectory</key><string>$(xml "$ROOT")</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$(xml "$PATH")</string></dict>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
EOF

# 3. 메뉴 막대 앱 LaunchAgent: 로그인 때 실행
cat > "$AGENTS/$MENU_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$MENU_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$(xml "$APP/Contents/MacOS/DirectorMenu")</string><string>$(xml "$ROOT")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
EOF
plutil -lint "$AGENTS/$SERVER_LABEL.plist" "$AGENTS/$MENU_LABEL.plist" >/dev/null

unload "$MENU_LABEL"; unload "$SERVER_LABEL"
if lsof -nP -iTCP:8081 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port 8081 is in use by another process: stop it first, then run install.sh again" >&2
  exit 1
fi
launchctl bootstrap "gui/$UID_N" "$AGENTS/$SERVER_LABEL.plist"
launchctl bootstrap "gui/$UID_N" "$AGENTS/$MENU_LABEL.plist"
echo "installed: $APP"
