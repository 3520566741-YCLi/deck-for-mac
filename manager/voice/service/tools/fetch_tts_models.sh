#!/bin/bash
# 下载 Vesper 用的千问自带音色模型（Qwen3-TTS 0.6B CustomVoice 8bit，约 1.9GB）到 voice/models/hf。
# 装好后 tts.py 默认就用 qwen:Vivian（不会像克隆音色那样偶尔跑成男声）。重跑会跳过已下载的文件。
set -euo pipefail
ROOT="${DECK_VOICE_ROOT:-$HOME/Library/Application Support/Deck/voice}"
PY="$ROOT/venv/bin/python"
REPO="${DECK_QWEN_TTS:-mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit}"
export HF_HOME="$ROOT/models/hf"
unset HF_HUB_OFFLINE
"$PY" - "$REPO" <<'PY'
import sys
from huggingface_hub import snapshot_download
p = snapshot_download(sys.argv[1])
print("已下载到", p)
PY
