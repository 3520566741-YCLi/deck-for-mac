"""把 Vesper 的「开启音」做成配对的「收回音」：同一个声音，音高从高往低走（：喜欢出来的声音，收回去要像它、但从高往低）。

做法：把开启音整段倒过来，音高走向就从「低→高」变成「高→低」；但倒放会让声音「先慢慢冒出来、最后突然断掉」，
所以再把开启音原来的响度包络（干脆的起音 + 自然的尾巴）套回去，听起来还是一声干净的提示音。
最后比开启音轻一点（-2 dB），收回比出来更安静，符合「越收尾越轻」。

用法：python3 earcon_fall.py open.wav close-new.wav [--gain-db -2]
输出和输入同格式：16 bit PCM、同采样率、单声道。只读 open.wav，不改它。
"""
import argparse
import wave

import numpy as np


def read(path):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path} 不是 16 bit PCM")
        ch, sr = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def envelope(x, sr, ms=6):
    n = max(1, int(sr * ms / 1000))
    k = np.hanning(2 * n + 1); k /= k.sum()
    return np.sqrt(np.convolve(x * x, k, mode="same"))


def centroid_trend(x, sr):
    """前后两半的频谱重心：用来确认开启音是往上走的、新音是往下走的。"""
    h = len(x) // 2
    out = []
    for seg in (x[:h], x[h:]):
        s = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1 / sr)
        out.append(float((s * f).sum() / max(s.sum(), 1e-12)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--gain-db", type=float, default=-2.0)
    a = ap.parse_args()

    x, sr = read(a.src)
    # 去掉首尾的静音，免得倒过来以后开头空一截（跟手）
    e0 = envelope(x, sr)
    on = np.where(e0 > e0.max() * 0.01)[0]
    x = x[on[0]: on[-1] + 1] if len(on) else x

    y = x[::-1].copy()
    ex, ey = envelope(x, sr), envelope(y, sr)
    floor = ex.max() * 0.02
    g = ex / np.maximum(ey, floor)
    g = np.minimum(g, 8.0)                                   # 倒放后很轻的地方不要放大成噪声
    g = np.convolve(g, np.hanning(int(sr * 0.004)) / np.hanning(int(sr * 0.004)).sum(), mode="same")
    y *= g

    peak_src = np.abs(x).max()
    y *= peak_src / max(np.abs(y).max(), 1e-9) * 10 ** (a.gain_db / 20)
    f = int(sr * 0.003)                                      # 3ms 淡入、10ms 淡出，防爆音
    y[:f] *= np.linspace(0, 1, f)
    t = int(sr * 0.010)
    y[-t:] *= np.linspace(1, 0, t)

    with wave.open(a.dst, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())

    c0, c1 = centroid_trend(x, sr), centroid_trend(y, sr)
    print(f"开启音：{len(x)/sr:.3f}s，频谱重心 前半 {c0[0]:.0f}Hz → 后半 {c0[1]:.0f}Hz（{'往上' if c0[1] > c0[0] else '往下'}）")
    print(f"收回音：{len(y)/sr:.3f}s，频谱重心 前半 {c1[0]:.0f}Hz → 后半 {c1[1]:.0f}Hz（{'往上' if c1[1] > c1[0] else '往下'}），峰值 {20*np.log10(np.abs(y).max()):.1f} dBFS")


if __name__ == "__main__":
    main()
