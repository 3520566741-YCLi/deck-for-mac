#!/bin/bash
# 下载唤醒 + 声纹模型到 ~/Library/Application Support/Deck/voice/models/（内置盘）。
# 只留需要的文件（int8 chunk-16 KWS + CAM++ + Silero VAD），压缩包和用不到的文件移到废纸篓，不直接删。
set -euo pipefail
ROOT="${DECK_VOICE_MODELS:-$HOME/Library/Application Support/Deck/voice/models}"
REL=https://github.com/k2-fsa/sherpa-onnx/releases/download
KWS=sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20
SPK=3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx
mkdir -p "$ROOT" "$ROOT/silero_vad"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/deck-wake.XXXXXX")"
trash() { for p in "$@"; do [ -e "$p" ] && mv "$p" "$HOME/.Trash/$(basename "$p").$(date +%s)" ; done; return 0; }

if [ ! -f "$ROOT/$KWS/tokens.txt" ]; then
  echo "↓ $KWS"
  curl -fL --retry 3 -o "$TMP/kws.tar.bz2" "$REL/kws-models/$KWS.tar.bz2"
  tar -xjf "$TMP/kws.tar.bz2" -C "$TMP"
  mkdir -p "$ROOT/$KWS"
  src="$TMP/$KWS"
  cp "$src/tokens.txt" "$src/en.phone" "$ROOT/$KWS/"
  # encoder/joiner 用 int8 chunk-16（320ms 一块，延迟和准确度平衡），decoder 只有 fp32
  for part in encoder joiner; do
    f=$(ls "$src"/$part*chunk-16*int8.onnx 2>/dev/null | head -1 || true)
    [ -z "$f" ] && f=$(ls "$src"/$part*int8.onnx | head -1)
    cp "$f" "$ROOT/$KWS/"
  done
  f=$(ls "$src"/decoder*chunk-16*.onnx 2>/dev/null | grep -v int8 | head -1 || true)
  [ -z "$f" ] && f=$(ls "$src"/decoder*.onnx | grep -v int8 | head -1)
  cp "$f" "$ROOT/$KWS/"
  # 官方测试音频留一份做自检
  [ -d "$src/test_wavs" ] && cp -R "$src/test_wavs" "$ROOT/$KWS/"
fi

if [ ! -f "$ROOT/$SPK" ]; then
  echo "↓ $SPK"
  curl -fL --retry 3 -o "$ROOT/$SPK.part" "$REL/speaker-recongition-models/$SPK"
  mv "$ROOT/$SPK.part" "$ROOT/$SPK"
fi

if [ ! -f "$ROOT/silero_vad/silero_vad.onnx" ]; then
  echo "↓ silero_vad.onnx"
  curl -fL --retry 3 -o "$ROOT/silero_vad/silero_vad.onnx.part" "$REL/asr-models/silero_vad.onnx"
  mv "$ROOT/silero_vad/silero_vad.onnx.part" "$ROOT/silero_vad/silero_vad.onnx"
fi

trash "$TMP"
echo "✓ 模型就绪：$ROOT"
du -sh "$ROOT/$KWS" "$ROOT/$SPK" "$ROOT/silero_vad"
