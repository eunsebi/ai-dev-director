#!/bin/bash
# sim-screenshot.sh — 검수 보고서용 시뮬레이터 실행 화면 캡처 (fork 2026-11-20)
#
# 이미 빌드된 시뮬레이터용 .app(예: 검수 단계의 xcodebuild test/build 결과)을 시뮬레이터에 설치·실행하고
# 밝은·어두운 모드 화면을 PNG로 남긴다. 작업본·원본 프로젝트 파일은 건드리지 않는다.
#
#   sim-screenshot.sh --derived-data <폴더> --bundle-id <id> --out <보고서 폴더>
#                     [--device "iPhone 17e" | --udid <UDID>] [--app 이름.app] [--wait 6] [--appearances "light dark"]
set -euo pipefail

DD="" BUNDLE="" OUT="" DEVICE="iPhone 17e" UDID="" APP_NAME="" WAIT=6 APPEARANCES="light dark"
while [ $# -gt 0 ]; do
  case "$1" in
    --derived-data) DD="$2"; shift 2 ;;
    --bundle-id) BUNDLE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --udid) UDID="$2"; shift 2 ;;
    --app) APP_NAME="$2"; shift 2 ;;
    --wait) WAIT="$2"; shift 2 ;;
    --appearances) APPEARANCES="$2"; shift 2 ;;
    *) echo "모르는 옵션: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DD" ] && [ -n "$BUNDLE" ] && [ -n "$OUT" ] || { echo "--derived-data, --bundle-id, --out 이 필요합니다" >&2; exit 2; }

PRODUCTS="$DD/Build/Products/Debug-iphonesimulator"
if [ -n "$APP_NAME" ]; then
  APP="$PRODUCTS/$APP_NAME"
else
  APP="$(find "$PRODUCTS" -maxdepth 1 -name '*.app' ! -name '*Tests*' ! -name '*-Runner.app' 2>/dev/null | head -1)"
fi
[ -n "$APP" ] && [ -d "$APP" ] || { echo "시뮬레이터용 .app이 없습니다: $PRODUCTS (먼저 빌드 검사를 돌리세요)" >&2; exit 3; }

[ -n "$UDID" ] || UDID="$(xcrun simctl list devices available -j | /usr/bin/python3 -c '
import json, sys
name = sys.argv[1]
devs = [d for rt in json.load(sys.stdin)["devices"].values() for d in rt if d.get("name") == name and d.get("isAvailable", True)]
devs.sort(key=lambda d: d.get("state") != "Booted")
print(devs[0]["udid"] if devs else "")
' "$DEVICE")"
[ -n "$UDID" ] || { echo "시뮬레이터를 찾지 못했습니다: $DEVICE" >&2; exit 3; }

xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl bootstatus "$UDID" -b >/dev/null
mkdir -p "$OUT"
xcrun simctl terminate "$UDID" "$BUNDLE" 2>/dev/null || true
xcrun simctl install "$UDID" "$APP"

ORIGINAL="$(xcrun simctl ui "$UDID" appearance 2>/dev/null || echo light)"
for mode in $APPEARANCES; do
  xcrun simctl ui "$UDID" appearance "$mode" || true
  xcrun simctl terminate "$UDID" "$BUNDLE" 2>/dev/null || true
  xcrun simctl launch "$UDID" "$BUNDLE" >/dev/null
  sleep "$WAIT"
  xcrun simctl io "$UDID" screenshot --type=png "$OUT/screen-$mode.png" >/dev/null
  echo "캡처: $OUT/screen-$mode.png"
done
xcrun simctl terminate "$UDID" "$BUNDLE" 2>/dev/null || true
xcrun simctl ui "$UDID" appearance "$ORIGINAL" 2>/dev/null || true
