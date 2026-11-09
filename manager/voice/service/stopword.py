"""「停」和开口打断：助手在想或在说的时候，听到「停 / 别说了 / stop」就立刻停（清播放 + 取消生成）；
她说话时他直接开口说别的（声纹像用户），也会停下来改成听他说。

回声消除只在她想/说的时候临时打开（aec_auto），平时关着，不影响别的 App 用麦克风。

用 sherpa-onnx 关键词检测（和唤醒共用同一个 KWS 模型，不多占硬盘），只在 thinking / speaking 时跑。
麦克风音频已经过 DeckVoice 的回声消除，所以助手自己的声音不会把自己叫停。
在 listening 状态说「停」由 asr.py 定稿时判断。快捷键 ⌃⌥⌘. 也会停。
"""
import glob
import os
import threading
import time
import traceback

import numpy as np

from . import hub
from .config import CFG, ROOT, find_model_dir

_lock = threading.Lock()
_spotter = None
_stream = None
_last = 0.0
_disabled_reason = ""

KWS_PREFIX = "sherpa-onnx-kws-zipformer"


def _pick(d, pattern, prefer=("chunk-16", "int8")):
    files = sorted(glob.glob(os.path.join(d, pattern)))
    for p in prefer:
        hit = [f for f in files if p in os.path.basename(f)]
        if hit:
            files = hit
    return files[0] if files else None


def _tokenize(phrase, phones):
    """中文 → 声母/带调韵母（ppinyin），英文 → CMU 音素；和 sherpa_onnx.text2token 的 phone+ppinyin 一致，
    但不依赖 sentencepiece，并且不认识的词只跳过这一条。"""
    import re
    from pypinyin import pinyin
    from pypinyin.contrib.tone_convert import to_finals_tone, to_initials
    out = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z']+", phrase):
        if re.match(r"[\u4e00-\u9fff]", part):
            for (py,) in pinyin(part):
                ini, fin = to_initials(py, strict=False), to_finals_tone(py, strict=False)
                out += [x for x in (ini, fin) if x] or [py]
        else:
            ph = phones.get(part.upper())
            if not ph:
                return None
            out += ph
    return out or None


def _keywords_file(d):
    """把 stop_phrases 转成 KWS 关键词文件（只在内容变了时重写）。"""
    out = os.path.join(ROOT, "keywords-stop.txt")
    phones = {}
    lex = os.path.join(d, "en.phone")
    if os.path.exists(lex):
        with open(lex, encoding="utf-8") as f:
            for ln in f:
                t = ln.split()
                if len(t) >= 2:
                    phones.setdefault(t[0].upper(), t[1:])
    with open(os.path.join(d, "tokens.txt"), encoding="utf-8") as f:
        vocab = {ln.split()[0] for ln in f if ln.strip()}
    th = CFG["stop_kws_threshold"]
    lines = []
    for p in CFG["stop_phrases"]:
        toks = _tokenize(p, phones)
        if toks and all(t in vocab for t in toks):
            # 单字「停」最容易被普通说话误触发，门槛提高一些
            t = min(0.9, th + 0.15) if len(normalize(p)) <= 1 else th
            lines.append(f"{' '.join(toks)} @{p.replace(' ', '_')} #{t:.2f}")
        else:
            print(f"[stop] 「{p}」没法转成关键词，跳过", flush=True)
    body = "\n".join(lines) + "\n"
    try:
        with open(out, encoding="utf-8") as f:
            if f.read() == body:
                return out
    except FileNotFoundError:
        pass
    os.makedirs(ROOT, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    return out


def _load():
    global _spotter, _stream, _disabled_reason
    d = find_model_dir(KWS_PREFIX)
    if not d:
        _disabled_reason = "没找到 KWS 模型（唤醒线程的 fetch_wake_models.sh 会下载）"
        return False
    import sherpa_onnx
    enc, dec, joi = _pick(d, "encoder*.onnx"), _pick(d, "decoder*.onnx", ("chunk-16",)), _pick(d, "joiner*.onnx")
    if not (enc and dec and joi):
        _disabled_reason = f"KWS 模型文件不全：{d}"
        return False
    _spotter = sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(d, "tokens.txt"), encoder=enc, decoder=dec, joiner=joi,
        keywords_file=_keywords_file(d), num_threads=1, keywords_threshold=CFG["stop_kws_threshold"],
        num_trailing_blanks=1,
    )
    _stream = _spotter.create_stream()
    print(f"[stop] 「停」检测就绪：{os.path.basename(d)}", flush=True)
    return True


def normalize(p):
    return "".join(ch for ch in p if not ch.isspace())


_state_since = 0.0

# ---------- 开口打断 ----------
# ：「但凡环境里有一点声音就能打断她」。人被打断时也不是一出声就停，会接着说一小会儿，
# 这段时间在判断「是不是他在跟我说话」。所以现在：
#   听到人声 → 她接着说，把这段人声攒起来 → 攒够 1 秒核对声纹 → 是用户才原地暂停、开始听
#   不像他（室友、电视、杂音）→ 不暂停；人还在说就攒到 2 秒再核对一次（长一点更准），还不像就不管这一段
# 暂停后识别出来的话再由 barge.py 判断（停止词 / 嗯啊 / 回声 / Jev：是不是对她说的）。
_barge_vad = None
_barge_buf = []        # 这一段人声（说话中，回声消除后的麦克风）
_barge_seg = {"checks": 0, "busy": False, "done": False}


def _vad():
    global _barge_vad
    if _barge_vad is None:
        import sherpa_onnx
        from .config import model_path
        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = model_path("silero_vad", "silero_vad.onnx")
        c.silero_vad.threshold = 0.6
        c.silero_vad.min_silence_duration = 0.3
        c.silero_vad.min_speech_duration = 0.2
        c.silero_vad.max_speech_duration = 8
        c.silero_vad.window_size = 512
        c.sample_rate = hub.SAMPLE_RATE
        c.num_threads = 1
        _barge_vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=12)
    return _barge_vad


_ducked = [False]


def _duck(on):
    """听到人声先让一下（压低音量，不停）；确认是他再暂停，不是他就恢复。"""
    if not CFG.get("barge_duck", True) or on == _ducked[0]:
        return
    _ducked[0] = on
    hub.send_helper({"t": "duck", "on": on})


def _barge_reset():
    _barge_buf.clear()
    _barge_seg.update(checks=0, done=False)
    _duck(False)


def _barge_threshold():
    from . import speaker
    p = speaker.profile()
    if not p.enrolled or p.sent_centroid is None:
        return None
    thr = float(p.meta.get("sent_threshold", 0.5)) - CFG["barge_score_margin"]
    return max(CFG["barge_score_floor"], thr)


_barge_warned = [False]


def _barge(chunk, st):
    """打断第一步：她在说/在想时听到人声 → 接着说，攒够一段核对声纹，是用户才暂停开始听。"""
    from . import barge
    if barge.held():
        return
    vad = _vad()
    vad.accept_waveform(chunk)
    talking = vad.is_speech_detected()
    while not vad.empty():
        vad.pop()
    if not talking:
        if _barge_buf and not _barge_seg["busy"]:
            _barge_reset()
        return
    if _barge_seg["done"]:
        return  # 这段已经判过不是他：等这段人声结束
    if not _barge_buf:
        _duck(True)
        # VAD 要先听到约 0.2 秒人声才报「在说话」：把这之前的一小段也带上，声纹能早一点攒够（11-06 他嫌打断还慢一点）
        _barge_buf.append(hub.recent_audio(CFG["barge_lead_s"]))
    else:
        _barge_buf.append(chunk)
    n = sum(len(c) for c in _barge_buf) / hub.SAMPLE_RATE
    need = CFG["barge_confirm_s"] if _barge_seg["checks"] == 0 else CFG["barge_recheck_s"]
    if n < need or _barge_seg["busy"]:
        return
    audio = np.concatenate(_barge_buf)
    _barge_seg["checks"] += 1
    _barge_seg["busy"] = True
    threading.Thread(target=_barge_check, args=(audio, st, n), name="barge-verify", daemon=True).start()


def _barge_check(audio, st, dur):
    from . import barge
    try:
        thr = _barge_threshold()
        if thr is None:
            if not _barge_warned[0]:
                _barge_warned[0] = True
                print("[stop] 还没录声纹：说话中开口打断先不启用（停止词和 → 键照常）", flush=True)
            _barge_seg["done"] = True
            return
        t0 = time.perf_counter()
        score = _voice_score(audio)
        ms = int((time.perf_counter() - t0) * 1000)
        him = score >= thr
        barge.log({"event": "voice", "state": st, "dur": round(dur, 2), "score": round(score, 3),
                   "thr": round(thr, 3), "him": him, "ms": ms})
        if hub.state() not in ("speaking", "thinking") or barge.held():
            return
        if not him:
            print(f"[stop] {st}中听到人声但不像 用户（{score:.2f} < {thr:.2f}，{dur:.1f} 秒），不打断", flush=True)
            if _barge_seg["checks"] >= 2 or score < thr - 0.15:  # 差得远（室友、电视）就不再等第二次，马上恢复音量
                _barge_seg["done"] = True
                _duck(False)
            return
        _ducked[0] = False  # 暂停接管音量（从压低后的音量淡出），放开时恢复到正常音量
        if barge.hold(st):
            print(f"[stop] {st}中是用户在说话（{score:.2f}）→ 先暂停，听他说完再决定", flush=True)
            barge.log({"event": "hold", "state": st, "score": round(score, 3)})
            try:
                from . import asr
                asr.start_listening("barge", 4, preroll_s=dur + 0.4)
            except Exception:  # noqa: BLE001
                print(f"[stop] 打断后开始听失败:\n{traceback.format_exc()}", flush=True)
                barge.release("error")
        _barge_buf.clear()
        _barge_seg["done"] = True
    except Exception:  # noqa: BLE001
        print(f"[stop] 核对打断声纹出错:\n{traceback.format_exc()}", flush=True)
        _barge_seg["done"] = True
    finally:
        _barge_seg["busy"] = False


def _voice_score(audio):
    try:
        from . import speaker
        return float((speaker.verify(audio) or {}).get("score") or 0.0)
    except Exception:  # noqa: BLE001  没装声纹或出错：当成不是他（宁可不停，按 → 键照样能停）
        return 0.0


def _on_audio(chunk):
    global _stream, _last
    st = hub.state()
    if st not in ("thinking", "speaking"):
        if not _barge_seg["busy"]:
            _barge_reset()
        return
    if hub.aec_on and CFG["barge_voice"] and (st == "speaking" or time.monotonic() - _state_since > 0.8):
        try:
            _barge(chunk, st)
        except Exception:  # noqa: BLE001
            print(f"[stop] 开口打断检测出错:\n{traceback.format_exc()}", flush=True)
    # 刚说完那一下可能还带着他这句话的尾音，thinking 开始 0.8 秒内不听
    if st == "thinking" and time.monotonic() - _state_since < 0.8:
        return
    with _lock:
        if _spotter is None:
            return
        _stream.accept_waveform(hub.SAMPLE_RATE, chunk)
        hit = ""
        while _spotter.is_ready(_stream):
            _spotter.decode_stream(_stream)
            r = _spotter.get_result(_stream)
            if r:
                hit = r
                _spotter.reset_stream(_stream)
    # 想的时候他可能还在接着说话，单字「停」容易误触发；这时只认「停下 / 别说了 / stop」这类两个字以上的
    # 没有回声消除时她说话会被麦克风收进来，单字「停」很容易被她自己的声音触发；这时只认两个字以上的（停下 / 别说了 / stop），或按 → 键
    # 说话中、回声消除关着时不在这里丢：下面会核对声纹，是他说的单字「停」也照样停
    if hit and st == "thinking" and len(normalize(hit.replace("_", " "))) <= 1:
        print(f"[stop] {st} 中听到单字「{hit}」（回声消除{'开' if hub.aec_on else '关'}），忽略", flush=True)
        hit = ""
    if hit and st == "speaking" and not hub.aec_on:
        # 回声消除没开（比如没经过「想」直接念、或这台机器开不了）：她自己念到「stop」也会被收进来。
        # 这时要核对一下声纹，是用户才停
        score = _voice_score(hub.recent_audio(1.2))
        if score < CFG["barge_min_score"]:
            print(f"[stop] 说话中听到「{hit}」但声纹不像他（{score:.2f}，回声消除关），多半是她自己的声音，忽略", flush=True)
            hit = ""
    if hit and time.monotonic() - _last > 1.0:
        _last = time.monotonic()
        print(f"[stop] 听到「{hit}」", flush=True)
        hub.stop("voice")


_vp_on = False
_vp_gen = 0


def _vp(on):
    global _vp_on
    if not CFG["aec_auto"] or on == _vp_on:
        return
    _vp_on = on
    hub.send_helper({"t": "vp", "on": on})


def _on_state(ev):
    # 换状态时清掉 KWS 里残留的上下文，避免跨句误触发
    global _stream, _state_since, _vp_gen
    _state_since = time.monotonic()
    st = ev.get("state")
    if st in ("thinking", "speaking") and _spotter is not None:
        with _lock:
            _stream = _spotter.create_stream()
    if st == "thinking":
        _vp_gen += 1
        _vp(True)  # 还没出声就开好回声消除（重建音频引擎要零点几秒）
    elif st == "speaking":
        print(f"[stop] 开始说话：回声消除{'开' if hub.aec_on else '关'}，开口打断{'可用' if hub.aec_on and CFG['barge_voice'] else '不可用（只认停止词+声纹，或按 → 键）'}",
              flush=True)
        if not _barge_seg["busy"]:
            _barge_reset()
        if _barge_vad is not None:
            _barge_vad.reset()
    elif st == "idle":
        _vp_gen += 1
        gen = _vp_gen

        def later():
            if gen == _vp_gen and hub.state() == "idle":
                _vp(False)
        threading.Timer(CFG["aec_off_delay_s"], later).start()


def _on_helper(ev):
    # DeckVoice 刚连上（或服务刚重启）：回到默认的「回声消除关」
    global _vp_on
    if ev.get("connected") and CFG["aec_auto"]:
        want = hub.state() in ("thinking", "speaking", "listening")
        _vp_on = not want  # 强制发一次，和 helper 对齐
        _vp(want)


def _on_hotkey(ev):
    # → 键（⌃⌥⌘.）：停下，并关掉 Vesper 窗口
    if ev.get("name") == "stop":
        hub.stop("hotkey")
        hub.emit({"type": "dismiss", "source": "hotkey"})


def info():
    return {"ready": _spotter is not None, "why": _disabled_reason}


def start():
    hub.on("hotkey", _on_hotkey)
    hub.on("state", _on_state)
    hub.on("helper", _on_helper)
    try:
        ok = _load()
    except Exception:  # noqa: BLE001
        ok = False
        print(f"[stop] KWS 加载失败:\n{traceback.format_exc()}", flush=True)
    if not ok:
        print(f"[stop] 语音「停」暂不可用：{_disabled_reason}；快捷键 ⌃⌥⌘. 和页面按钮照常可用", flush=True)
    hub.subscribe_audio(_on_audio, name="stopword")
    # 模型可能稍后才下载好：每分钟重试一次
    def retry():
        while _spotter is None:
            time.sleep(60)
            try:
                _load()
            except Exception:  # noqa: BLE001
                pass
    if not ok:
        threading.Thread(target=retry, name="stop-retry", daemon=True).start()
