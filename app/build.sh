#!/bin/bash
# 编译并打包 Deck.app，装到 /Applications（沿用 bundle id io.github.deckformac.deck，权限和登录项不受影响）。
# 用法：./build.sh            # 自动找本机 Apple Development 证书签名
#       SIGN_ID="-" ./build.sh   # 没证书时临时签名（别人分享用）
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release
APP="dist/Deck.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/FusionWorkbench "$APP/Contents/MacOS/Deck"
cp Info.plist "$APP/Contents/Info.plist"
# 图标：用管家里的监控台图标生成 icns
ICON_SRC="Resources/AppIcon.png"
if [ -f "$ICON_SRC" ]; then
  SET=$(mktemp -d)/AppIcon.iconset; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$ICON_SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"
fi
ID="${SIGN_ID:-$(security find-identity -v -p codesigning | awk -F'"' '/Apple Development/{print $2; exit}')}"
codesign --force --deep --options runtime --sign "${ID:--}" "$APP"
echo "签名：${ID:--}"
if [ "${INSTALL:-1}" = "1" ]; then
  osascript -e 'quit app id "io.github.deckformac.deck"' 2>/dev/null || true
  rm -rf "/Applications/Deck.app"
  cp -R "$APP" /Applications/
  echo "已安装到 /Applications/Deck.app"
fi
