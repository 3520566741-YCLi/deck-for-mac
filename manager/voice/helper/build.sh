#!/bin/bash
# 编译 DeckVoice.app 并装到 ~/Applications（旧版移到废纸篓）。
# 用 Apple Development 证书签名：麦克风/语音识别权限跟着签名走，重新编译后不用重新授权。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BUILD="$HERE/build"
APP="$BUILD/DeckVoice.app"
VERSION="1.0.0"

# 不删旧产物：直接覆盖（swiftc / codesign --force）
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# 接 Objective-C 异常的小垫片（Swift 接不住 NSException）
OBJ="$(mktemp -d)/DVTry.o"
clang -c -O2 -fobjc-arc -target arm64-apple-macos14.0 "$HERE/ObjC/DVTry.m" -o "$OBJ"
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 \
  -import-objc-header "$HERE/ObjC/DVTry.h" \
  -framework AppKit -framework AVFoundation -framework Speech -framework Network -framework Carbon -framework CoreAudio -framework AudioToolbox -framework CoreGraphics \
  "$HERE"/Sources/*.swift "$OBJ" -o "$APP/Contents/MacOS/DeckVoice"
rm -f "$OBJ"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>io.github.deckformac.deckvoice</string>
  <key>CFBundleName</key><string>DeckVoice</string>
  <key>CFBundleDisplayName</key><string>DeckVoice</string>
  <key>CFBundleExecutable</key><string>DeckVoice</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Deck 的语音助手需要听你说话（唤醒词、语音指令、「停」）。音频只在本机处理。</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>用 Apple 本机中文识别在屏幕上实时显示你说的话。</string>
  <key>DeckRepoPath</key><string>$REPO</string>
</dict></plist>
PLIST

ID="$(security find-identity -v -p codesigning | grep -m1 'Apple Development' | sed -E 's/.*"(.*)"/\1/' || true)"
if [ -n "$ID" ]; then
  codesign --force --options runtime --entitlements "$HERE/DeckVoice.entitlements" --sign "$ID" "$APP"
else
  echo "⚠️ 没找到 Apple Development 证书，用临时签名（每次重编译可能要重新授权麦克风）"
  codesign --force --entitlements "$HERE/DeckVoice.entitlements" --sign - "$APP"
fi
codesign --verify --verbose=1 "$APP"

DEST="$HOME/Applications/DeckVoice.app"
mkdir -p "$HOME/Applications"
if [ -d "$DEST" ]; then
  pkill -x DeckVoice 2>/dev/null || true
  sleep 0.5
  osascript -e "tell application \"Finder\" to delete POSIX file \"$DEST\"" >/dev/null 2>&1 || mv "$DEST" "$HOME/.Trash/DeckVoice-$(date +%s).app"
fi
ditto "$APP" "$DEST"
echo "已安装 $DEST"
