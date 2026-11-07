#!/bin/bash
# DeckVoice 看门狗：登录时和之后每 5 分钟检查一次，DeckVoice 没在运行就在后台打开它（它会把语音服务也拉起来）。
# 不想自动打开：touch ~/Library/Application\ Support/Deck/voice/keepalive-off
OFF="$HOME/Library/Application Support/Deck/voice/keepalive-off"
APP="$HOME/Applications/DeckVoice.app"
[ -f "$OFF" ] && exit 0
[ -d "$APP" ] || exit 0
pgrep -qf "$APP/Contents/MacOS/DeckVoice" && exit 0
echo "$(date '+%m-%d %H:%M:%S') DeckVoice 没在运行，重新打开"
open -g -a "$APP"
