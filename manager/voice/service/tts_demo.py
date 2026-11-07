"""试听/自测：python -m service.tts_demo [--voice kokoro:zf_001] [--out x.wav] "要念的话"
不连 hub，只合成到 wav 文件；顺带打印合成耗时和实时率。"""
import argparse
import time

from . import tts

ap = argparse.ArgumentParser()
ap.add_argument("text", nargs="?", default="你好呀，我在。今天想让我帮你做点什么？")
ap.add_argument("--voice")
ap.add_argument("--speed", type=float)
ap.add_argument("--out", default="tts-demo.wav")
ap.add_argument("--list", action="store_true")
a = ap.parse_args()
if a.list:
    for v in tts.voices():
        print(("* " if v["current"] else "  ") + v["id"], v["label"], "" if v["installed"] else "(未安装)")
    raise SystemExit
t0 = time.time()
sr, pcm = tts.synth(a.text, a.voice, a.speed)
dt = time.time() - t0
open(a.out, "wb").write(tts.wav_bytes(pcm, sr))
dur = len(pcm) / sr
print(f"{a.out}: {dur:.2f}s 音频，用时 {dt:.2f}s，实时率 {dt / max(dur, 1e-6):.2f}")
