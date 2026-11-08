#!/bin/bash
# 编译 DeckHands.app（Vesper 的「手」）并装到 ~/Applications，旧版移到废纸篓。
# 用 Apple Development 证书签名：辅助功能权限跟着签名走，重新编译后不用重新授权。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
APP="$BUILD/DeckHands.app"
VERSION="1.1.0"
mkdir -p "$APP/Contents/MacOS"
# 多个源文件时顶层代码只能在 main.swift 里，所以把 DeckHands.swift 复制成 main.swift 再编译
cp "$HERE/DeckHands.swift" "$BUILD/main.swift"
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -framework AppKit -framework ApplicationServices -framework IOKit \
  "$BUILD/main.swift" "$HERE/CleanMode.swift" -o "$APP/Contents/MacOS/DeckHands"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>io.github.deckformac.deckhands</string>
  <key>CFBundleName</key><string>DeckHands</string>
  <key>CFBundleExecutable</key><string>DeckHands</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
ID="$(security find-identity -v -p codesigning | grep -m1 'Apple Development' | sed -E 's/.*"(.*)"/\1/' || true)"
if [ -n "$ID" ]; then
  codesign --force --options runtime --sign "$ID" "$APP"
else
  echo "⚠️ 没找到 Apple Development 证书，用临时签名（每次重编译要重新开辅助功能权限）"
  codesign --force --sign - "$APP"
fi
codesign --verify "$APP"
DEST="$HOME/Applications/DeckHands.app"
mkdir -p "$HOME/Applications"
if [ -d "$DEST" ]; then
  pkill -x DeckHands 2>/dev/null || true
  sleep 0.5
  osascript -e "tell application \"Finder\" to delete POSIX file \"$DEST\"" >/dev/null 2>&1 || mv "$DEST" "$HOME/.Trash/DeckHands-$(date +%s).app"
fi
ditto "$APP" "$DEST"
echo "已安装 $DEST"
