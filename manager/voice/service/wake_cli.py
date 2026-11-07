"""唤醒/声纹的独立调试入口（不依赖语音服务和 DeckVoice.app）。

  python wake_cli.py live          # 直接开麦（sounddevice），跑唤醒+声纹，并在 127.0.0.1:8790/enroll 提供录入页
  python wake_cli.py eval a.wav…   # 对录音文件跑 KWS + 声纹打分
  python wake_cli.py bench         # 测 KWS 实时率、声纹耗时、内存
  python wake_cli.py sweep         # 用 macOS 合成声音扫 KWS 门槛/加分：每种叫法的命中率 vs 普通聊天的误命中
  python wake_cli.py names Luna,Nova…  # 比较候选名字：每个名字的全部叫法用合成声音说，看唤醒模型认不认得、会不会误叫醒
"""
import json
import os
import resource
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import speaker  # noqa: E402
import voicepaths as P  # noqa: E402
import wake  # noqa: E402

SR = 16000


class MiniHub:
    """最小 hub：只实现 wake/enroll 用到的接口。"""

    def __init__(self):
        self.subs, self.routes, self._state = [], [], "idle"

    def state(self):
        return self._state

    def subscribe_audio(self, cb, name=None):
        self.subs.append(cb)

    def route(self, method, prefix, handler):
        self.routes.append((method, prefix, handler))

    def emit(self, ev):
        print("EVENT", json.dumps(ev, ensure_ascii=False), flush=True)
        if ev.get("type") == "wake" and ev.get("speaker_ok"):
            self._state = "listening"
            threading.Timer(3, lambda: setattr(self, "_state", "idle")).start()

    def push(self, x):
        for cb in self.subs:
            cb(x)


def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3


def serve(hub, port=8790):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self, method):
            u = urllib.parse.urlparse(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else b""
            for m, prefix, h in hub.routes:
                if m == method and u.path.startswith(prefix):
                    code, data, ctype = h(method, u.path, urllib.parse.parse_qs(u.query), body)
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self.send_error(404)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"录入页：http://127.0.0.1:{port}/enroll")


def live():
    import sounddevice as sd
    import enroll
    enroll.CHAIN = "raw-mic"
    hub = MiniHub()
    wake.start(hub)
    enroll.register(hub)
    serve(hub)
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=1600,
                        callback=lambda d, f, t, s: hub.push(d[:, 0].copy())):
        print("开麦了，说「你好Kira」或「Hey Kira」。Ctrl+C 退出。", flush=True)
        while True:
            time.sleep(30)
            print(f"[mem] 峰值 RSS {rss_mb():.0f} MB", flush=True)


def load_any(path):
    import wave
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        n = int(len(x) * SR / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def eval_files(paths):
    prof = speaker.profile()
    for p in paths:
        x = load_any(p)
        hit, span = wake.spot_offline(x)
        seg = wake.trim_speech(x)
        r = speaker.check(speaker.embed(seg)) if prof.enrolled else {"score": None}
        print(f"{os.path.basename(p)}  kws={'命中' if hit else '—'} span={span}  speaker={r}")


def bench():
    t0 = time.time()
    kws = wake.spotter()
    speaker.embed(np.zeros(SR, np.float32))
    print(f"加载模型 {time.time() - t0:.2f}s，峰值 RSS {rss_mb():.0f} MB")
    x = (np.random.randn(SR * 30) * 0.01).astype(np.float32)
    st = kws.create_stream()
    t0 = time.time()
    for i in range(0, len(x), 1600):
        st.accept_waveform(SR, x[i:i + 1600])
        while kws.is_ready(st):
            kws.decode_stream(st)
    dt = time.time() - t0
    print(f"KWS 30 秒音频用 {dt:.2f}s（实时率 {dt / 30:.3f}，单线程）")
    t0 = time.time()
    for _ in range(10):
        speaker.embed(x[: int(SR * 1.5)])
    print(f"声纹 1.5 秒片段 {(time.time() - t0) * 100:.0f} ms/次；峰值 RSS {rss_mb():.0f} MB")
    tw = P.KWS_DIR / "test_wavs"
    if not tw.exists():
        return
    # test_wavs 里说的是官方关键词（落实/女儿/LIGHT UP…），不含「Kira」，
    # 所以用官方 keywords.txt 另建一个 spotter 验证解码链路；「Kira」那列应全为 —（误唤醒检查）。
    import sherpa_onnx
    ref = sherpa_onnx.KeywordSpotter(
        tokens=str(P.KWS_DIR / "tokens.txt"),
        encoder=P.kws_file("encoder*chunk-16*int8.onnx", "encoder*int8.onnx", "encoder*.onnx"),
        decoder=P.kws_file("decoder*chunk-16*.onnx", "decoder*.onnx"),
        joiner=P.kws_file("joiner*chunk-16*int8.onnx", "joiner*int8.onnx", "joiner*.onnx"),
        keywords_file=str(tw / "keywords.txt"), num_threads=1, provider="cpu")
    hits_total = 0
    wavs = sorted(tw.glob("*.wav"))
    for d in wavs:
        x = load_any(str(d))
        hit, _ = wake.spot_offline(x)
        st = ref.create_stream()
        st.accept_waveform(SR, np.concatenate([x, np.zeros(int(SR * 0.6), np.float32)]))
        st.input_finished()
        found = []
        while ref.is_ready(st):
            ref.decode_stream(st)
            r = ref.get_result(st)
            if r:
                found.append(r)
                ref.reset_stream(st)
        hits_total += bool(found)
        print(f"  自检 {d.name}: 官方词={','.join(found) or '—'}  Kira={'命中(误唤醒!)' if hit else '—'}")
    print(f"  官方关键词命中 {hits_total}/{len(wavs)} 个文件")

def calibrate():
    """用录入原声算「你自己」的分数，用 macOS 自带中文语音和官方测试音频当「别人」，看两边分得开不开。"""
    import glob
    import subprocess
    import tempfile
    prof = speaker.profile()
    assert prof.enrolled, "先录入声纹"
    ed = P.VOICEPRINT / "enroll"
    wake_wavs = sorted(ed.glob("w*.wav"))
    print("== 你自己（留一法，用录入原声）==")
    own = []
    for i, f in enumerate(wake_wavs):
        e = speaker.embed(wake.trim_speech(load_any(str(f))))
        s, td, ti = prof.score(e, exclude=i if i < len(prof.wake) else None)
        own.append(s)
        print(f"  {f.name}: {s:.3f} (td {td:.3f}, ti {ti if ti is None else round(ti, 3)})")
    print(f"== 别人：macOS 中文语音说「{P.WAKE_WORD}」==")
    voices = [ln.split("  ")[0].strip() for ln in subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout.splitlines()
              if "zh_" in ln]
    tmp = tempfile.mkdtemp()
    imp = []
    for v in voices:
        for rate in (170, 220):
            a, w = f"{tmp}/x.aiff", f"{tmp}/x.wav"
            if subprocess.run(["say", "-v", v, "-r", str(rate), "-o", a, P.WAKE_WORD]).returncode:
                continue
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", a, w], check=True)
            x = load_any(w)
            hit, _ = wake.spot_offline(x)
            r = speaker.check(speaker.embed(wake.trim_speech(x)))
            imp.append(r["score"])
            print(f"  {v} r{rate}: {r['score']:.3f} kws={'命中' if hit else '—'} {'⚠️ 会被叫醒' if r['ok'] else ''}")
    tw = P.KWS_DIR / "test_wavs"
    print("== 别人：官方测试音频（不同的话，只看音色）==")
    for f in sorted(tw.glob("zh_*.wav")) if tw.exists() else []:
        x = load_any(str(f))
        r = speaker.check(speaker.embed(x[: int(SR * 2)]))
        imp.append(r["score"])
        print(f"  {f.name}: {r['score']:.3f}")
    if own and imp:
        print(f"== 你自己 最低 {min(own):.3f} 平均 {np.mean(own):.3f}；别人 最高 {max(imp):.3f} 平均 {np.mean(imp):.3f}；"
              f"当前门槛 {prof.threshold}；间隔 {min(own) - max(imp):+.3f}")
    import shutil
    shutil.move(tmp, os.path.expanduser("~/.Trash/" + os.path.basename(tmp)))


NEG_ZH = ["今天是星期五", "我去楼下拿个快递", "这个七拉八扯的事情", "你好，请问几点了", "嘿，你吃饭了吗",
          "其乐融融的一家人", "我很期待明天", "起来啦，太阳晒屁股了", "你好呀，好久不见", "早上好，今天天气不错",
          "在吗，帮我看一下", "醒醒，该上班了", "把旗子拉起来", "基拉韦厄火山又喷发了", "奇拉比赛几点开始"]
NEG_EN = ["Hey Siri what's the weather", "The kids are here", "Here is the key, right", "Clear the cache please",
          "Hey Kim how are you", "Keep it real", "Hello Kitty is cute", "Hey Kara, nice to see you", "Clara is coming",
          "Good morning everyone", "Okay Google", "Sierra Nevada", "Her career is great",
          "Wake up, it's late", "Are you there"]


def _synth(text, voice, rate, out):
    import subprocess
    a = out + ".aiff"
    if subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", a, text]).returncode:
        return None
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", a, out], check=True)
    os.remove(a)
    return load_any(out)


def sweep():
    """合成每种叫法（多个声音×两种语速）和一批普通句子，按不同 KWS 门槛/加分算命中率和误命中。不碰正式文件。"""
    import subprocess
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kira-sweep-")
    vs = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout.splitlines()
    import re
    # 「Tingting (中文（中国大陆）) zh_CN」：名字后面可能只有一个空格跟括号
    have = {re.split(r"\s{2,}|\s\(", ln)[0].strip() for ln in vs}
    zh_v = [v for v in ("Tingting", "Meijia", "Lili", "Yu-shu") if v in have][:3]
    en_v = [v for v in ("Samantha", "Daniel", "Karen", "Moira", "Alex", "Fred") if v in have][:4]
    print("声音：", zh_v, en_v, flush=True)
    pos, neg = [], []
    k = 0
    for words, voices in ((P.WAKE_WORDS_ZH, zh_v), (P.WAKE_WORDS_EN, en_v)):
        for w in words:
            for v in voices:
                for r in (170, 215):
                    k += 1
                    x = _synth(w, v, r, f"{tmp}/{k}.wav")
                    if x is not None:
                        pos.append((w, v, r, x))
    for words, voices in ((NEG_ZH, zh_v[:2]), (NEG_EN, en_v[:2])):
        for w in words:
            for v in voices:
                k += 1
                x = _synth(w, v, 190, f"{tmp}/{k}.wav")
                if x is not None:
                    neg.append((w, v, x))
    print(f"正样本 {len(pos)} 条，负样本 {len(neg)} 条", flush=True)
    grid = [(th, sc) for th in (0.15, 0.10, 0.07, 0.05) for sc in (2.0, 3.0, 4.0)]
    base = wake.settings()
    best = None
    for th, sc in grid:
        cfg = dict(base, kws_threshold_en=th, kws_score_en=sc)
        wake.settings = lambda cfg=cfg: cfg
        wake._spotter = None
        hit = {}
        for w, v, r, x in pos:
            kw, _ = wake.spot_offline(x)
            hit.setdefault(w, []).append(bool(kw))
        fa = [(w, v, wake.spot_offline(x)[0]) for w, v, x in neg]
        fa = [f for f in fa if f[2]]
        rate = sum(sum(h) for h in hit.values()) / max(1, sum(len(h) for h in hit.values()))
        none = [w for w, h in hit.items() if not any(h)]
        print(f"门槛 {th:.2f} 加分 {sc:.1f}：命中率 {rate:.0%}，一次都没中的叫法 {len(none)} {none}；"
              f"误命中 {len(fa)} {[(w, v, kw) for w, v, kw in fa]}", flush=True)
        if not fa and (best is None or rate > best[0]):
            best = (rate, th, sc)
    print("== 零误命中里命中率最高：", best)
    shutil_move(tmp)


def names(arg=None):
    """候选名字比较：每个名字套进全部叫法（voicepaths.phrases），合成声音说一遍，算命中率和误命中。不碰正式文件。"""
    import re
    import subprocess
    import tempfile
    cands = [n.strip() for n in (arg or "").split(",") if n.strip()] or \
        ["Kira", "Luna", "Nova", "Mia", "Lucy", "Coco", "Momo", "Lily", "Jarvis", "Ella", "Lumi", "Milo"]
    tmp = tempfile.mkdtemp(prefix="name-sweep-")
    vs = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout.splitlines()
    have = {re.split(r"\s{2,}|\s\(", ln)[0].strip() for ln in vs}
    zh_v = [v for v in ("Tingting", "Meijia") if v in have]
    en_v = [v for v in ("Samantha", "Daniel", "Karen") if v in have]
    print("声音：", zh_v, en_v, flush=True)
    k = 0
    neg = []
    for words, voices in ((NEG_ZH, zh_v[:1]), (NEG_EN, en_v[:1])):
        for w in words:
            for v in voices:
                k += 1
                x = _synth(w, v, 190, f"{tmp}/{k}.wav")
                if x is not None:
                    neg.append((w, v, x))
    base = wake.settings()
    rows = []
    for name in cands:
        zh, en = P.phrases(name)
        P.WAKE_WORD, P.WAKE_WORDS_ZH, P.WAKE_WORDS_EN = "你好" + name, zh, en
        P.WAKE_WORDS = zh + en
        pos = []
        for words, voices in ((zh, zh_v), (en, en_v)):
            for w in words:
                for v in voices:
                    k += 1
                    x = _synth(w, v, 190, f"{tmp}/{k}.wav")
                    if x is not None:
                        pos.append((w, v, x))
        line = [name]
        for th, sc in ((0.15, 2.0), (0.07, 3.0)):
            cfg = dict(base, kws_threshold_en=th, kws_score_en=sc)
            wake.settings = lambda cfg=cfg: cfg
            wake._spotter = None
            try:
                wake.spotter()
            except Exception as ex:  # 名字的音素不在词表里
                line.append(f"{th}/{sc}: 加载失败 {ex}")
                continue
            hz = [bool(wake.spot_offline(x)[0]) for w, v, x in pos if w in zh]
            he = [bool(wake.spot_offline(x)[0]) for w, v, x in pos if w in en]
            miss = sorted({w for w, v, x in pos if not wake.spot_offline(x)[0]})
            fa = [(w, v) for w, v, x in neg if wake.spot_offline(x)[0]]
            line.append(f"{th}/{sc}: 中文 {sum(hz)}/{len(hz)} 英文 {sum(he)}/{len(he)} 合计 {(sum(hz) + sum(he)) / max(1, len(hz) + len(he)):.0%}"
                        f" 误命中 {len(fa)} {fa} 没中 {miss}")
        rows.append(line)
        print(" | ".join(line), flush=True)
    print("== 汇总 ==")
    for r in rows:
        print(" | ".join(r))
    shutil_move(tmp)


def shutil_move(path):
    import shutil
    shutil.move(path, os.path.expanduser("~/.Trash/" + os.path.basename(path)))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "live"
    if cmd == "names":
        names(sys.argv[2] if len(sys.argv) > 2 else None)
        sys.exit(0)
    {"live": live, "bench": bench, "calibrate": calibrate, "sweep": sweep}.get(cmd, lambda: eval_files(sys.argv[2:]))()
