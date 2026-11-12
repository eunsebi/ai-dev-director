#!/bin/bash
# 맥에서 한 번 실행: 잠가 둔 OpenClaw VM 열쇠(authorized_keys의 openclaw-vm-to-mac 줄)를 "Director 접수 전용"으로 연다.
#   bash ~/work/ai-dev-director/tools/setup-intake-key.sh
# 그 열쇠로는 ~/.local/bin/director-intake만 돈다(restrict: 셸·포워딩·pty 없음). 요청은 Director 「디스코드 접수함」에 쌓이고
# 오빠가 콘솔에서 [시작]을 눌러야 미션이 시작된다.
# 되돌리기(다시 잠금): cp ~/.ssh/authorized_keys.bak-intake-2026-11-12 ~/.ssh/authorized_keys
set -euo pipefail
AK="$HOME/.ssh/authorized_keys"
BIN="$HOME/.local/bin/director-intake"
[ -x "$BIN" ] || { echo "$BIN 이 없습니다"; exit 1; }
[ -f "$HOME/.config/director-intake/projects.json" ] || { echo "~/.config/director-intake/projects.json 이 없습니다"; exit 1; }
LINE=$(grep -n 'openclaw-vm-to-mac' "$AK" | head -1 | cut -d: -f1)
[ -n "$LINE" ] || { echo "openclaw-vm-to-mac 줄을 찾지 못했습니다"; exit 1; }
KEY=$(sed -n "${LINE}p" "$AK" | grep -o 'ssh-ed25519 AAAA[A-Za-z0-9+/=]*')
[ -n "$KEY" ] || { echo "열쇠를 읽지 못했습니다"; exit 1; }
cp -p "$AK" "$HOME/.ssh/authorized_keys.bak-intake-2026-11-12"
NEW="command=\"$BIN\",restrict $KEY openclaw-vm-to-mac (2026-11-12 Director 접수 전용)"
awk -v n="$LINE" -v new="$NEW" 'NR==n{print new; next} {print}' "$AK" > "$AK.tmp"
chmod 600 "$AK.tmp" && mv "$AK.tmp" "$AK"
echo "바꾼 줄:"; sed -n "${LINE}p" "$AK" | cut -c1-110
echo "완료: VM 열쇠는 이제 director-intake만 실행합니다. (백업 ~/.ssh/authorized_keys.bak-intake-2026-11-12)"
