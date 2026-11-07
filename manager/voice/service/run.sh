#!/bin/bash
# 启动 Deck 语音服务。可以重复执行：已经在跑就直接退出 0。DeckVoice.app 会调用它并在它退出后重启。
set -euo pipefail
ROOT="$HOME/Library/Application Support/Deck/voice"
VENV="$ROOT/venv"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT"
if [ ! -x "$VENV/bin/python" ]; then
  PY="$(command -v python3.11 || command -v python3.12 || command -v python3)"
  "$PY" -m venv "$VENV"
fi
# requirements 变了才重新装
STAMP="$VENV/.req-$(shasum "$HERE/requirements.txt" | cut -c1-12)"
if [ ! -f "$STAMP" ]; then
  "$VENV/bin/pip" install -q --disable-pip-version-check -r "$HERE/requirements.txt"
  touch "$STAMP"
fi
cd "$HERE/.."
exec "$VENV/bin/python" -u -m service.server
