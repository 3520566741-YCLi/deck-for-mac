#!/bin/bash
# 下载识别要用的模型到 ~/Library/Application Support/Deck/voice/models/
#   silero VAD（2MB，句尾判断）
#   Qwen3-ASR-0.6B int8（约 940MB，默认定稿模型，原生热词）
#   SenseVoice-Small int8（约 230MB，备用：更快更省内存）  用 --sensevoice 只下这个
# 压缩包解压后移到废纸篓，不直接删。
set -euo pipefail
R="$HOME/Library/Application Support/Deck/voice/models"
BASE=https://github.com/k2-fsa/sherpa-onnx/releases/download
mkdir -p "$R" && cd "$R"
trash() { osascript -e "tell application \"Finder\" to delete POSIX file \"$1\"" >/dev/null 2>&1 || mv "$1" ~/.Trash/; }
fetch_tar() {  # $1 = 包名（不带 .tar.bz2）
  [ -d "$R/$1" ] && { echo "已有 $1"; return; }
  curl -fL --retry 3 -o "$1.tar.bz2" "$BASE/asr-models/$1.tar.bz2"
  tar xjf "$1.tar.bz2" && trash "$R/$1.tar.bz2"
}
mkdir -p silero_vad
[ -f silero_vad/silero_vad.onnx ] || curl -fL --retry 3 -o silero_vad/silero_vad.onnx "$BASE/asr-models/silero_vad.onnx"
if [ "${1:-}" != "--sensevoice" ]; then
  fetch_tar sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25
fi
fetch_tar sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09 || fetch_tar sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
# SenseVoice 只留 int8 模型
for d in sherpa-onnx-sense-voice-*; do [ -f "$d/model.onnx" ] && [ -f "$d/model.int8.onnx" ] && trash "$R/$d/model.onnx"; done
du -sh "$R"/*
