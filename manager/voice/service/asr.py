"""语音识别：唤醒后听一句话 → 实时字幕（Apple 本机）→ 说完用阿里模型定稿 → user.final。

两段式，取两家所长：
  - 实时字幕：DeckVoice.app 里的 Apple SFSpeechRecognizer（本机、低延迟、带 contextualStrings 热词）。
  - 定稿：sherpa-onnx 跑阿里模型，准确率更高，中英混说更稳：
      qwen3      Qwen3-ASR-0.6B int8（通义 2026，原生热词，约 940MB）
      sensevoice SenseVoice-Small int8（FunAudioLLM，快 7 倍、内存 1/5，但中英混说的英文名词基本认不出；热词只靠纠错表）
    首次用到才加载，空闲 asr_idle_unload_s 秒后卸载，平时不占内存。
  - 句尾判断：silero VAD（约 2MB）。
"""
import json
import os
import re
import threading
import time
import traceback
import unicodedata

import numpy as np

from . import hub
from .config import CFG, HERE, MODELS, ROOT, find_model_dir, model_path

SR = hub.SAMPLE_RATE
_lock = threading.RLock()


# ---------- 热词和纠错 ----------

def _read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except FileNotFoundError:
        return []


def _app_names():
    p = os.path.join(HERE, "..", "..", "apps.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("apps", d) if isinstance(d, dict) else d
        return [a.get("name") for a in items if isinstance(a, dict) and a.get("name")]
    except Exception:  # noqa: BLE001
        return []


def load_hotwords():
    words = _read_lines(os.path.join(HERE, "hotwords.txt")) + _read_lines(os.path.join(ROOT, "hotwords.txt")) + _app_names()
    seen, out = set(), []
    for w in words:
        k = w.lower()
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def _latin_pattern(word):
    """英文热词 → 宽松正则：不分大小写，词内允许空格/连字符（"deep seek" / "Deep-Seek" → DeepSeek）。"""
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", word) or [word]
    letters = r"[\s\-]?".join(re.escape(p) for p in parts)
    return re.compile(r"(?<![A-Za-z])" + letters + r"(?![A-Za-z])", re.I)


def load_corrections(hotwords):
    rules = []
    for ln in _read_lines(os.path.join(HERE, "corrections.txt")) + _read_lines(os.path.join(ROOT, "corrections.txt")):
        if "=>" not in ln:
            continue
        a, b = (s.strip() for s in ln.split("=>", 1))
        if a:
            rules.append((re.compile(re.escape(a), re.I), b))
    for w in hotwords:
        if re.search(r"[A-Za-z]", w) and not re.search(r"[一-鿿]", w):
            rules.append((_latin_pattern(w), w))
    return rules


def apply_corrections(text, rules):
    # 有的模型英文整句大写（THE TRIBAL CHIEF...）→ 句首大写
    if re.search(r"[A-Z]{4,}", text) and not re.search(r"[a-z]", text):
        text = re.sub(r"[A-Z][A-Z']*", lambda m: m.group(0).lower(), text)
        text = re.sub(r"(^|[.!?。！？]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    for pat, rep in rules:
        text = pat.sub(rep, text)
    # 中英文之间留一个空格，读起来舒服（和 Apple 字幕一致）
    text = re.sub(r"([一-鿿])([A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9])([一-鿿])", r"\1 \2", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def normalize_for_match(text):
    t = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s\W_]+", "", t)


def is_stop_phrase(text):
    n = normalize_for_match(text)
    return bool(n) and any(n == normalize_for_match(p) for p in CFG["stop_phrases"])


_DISMISS_KEYS = ("退下", "再见", "拜拜", "你可以走了", "没事了")


# 英文告别：去掉称呼和客套词后，剩下的必须正好是这些之一（避免长句里顺带的 "see you" 误关）
_DISMISS_EN = {"bye", "byebye", "goodbye", "seeyou", "seeya", "cya", "goodnight", "nightnight",
               "thatsall", "thatsit", "dismissed", "youcango", "youmaygo"}
_EN_FILLER_HEAD = re.compile(r"^(ok(ay)?|alright|thanks|thankyou|cool|great|hey)+")
_EN_FILLER_TAIL = re.compile(r"(later|soon|fornow|tomorrow|thanks|thankyou)+$")


# 她的名字（识别可能写成中文音译）
_NAMES = ("vesper", "维斯珀", "薇丝珀", "维斯帕", "韦斯帕", "维斯珀")


def is_dismiss_phrase(text):
    """「退下吧 Vesper」「再见 Vesper」「See you Vesper」「Bye Vesper」这类短句：停下并关掉对话窗口。
    长句里顺带提到的不算。"""
    n = normalize_for_match(text)
    if not n:
        return False
    if len(n) <= 9 and any(k in n for k in _DISMISS_KEYS):
        return True
    core = n
    try:
        from . import voicepaths as _P
        cur = (normalize_for_match(_P.NAME),)   # Deck 设置里改过名字：新名字也要去掉
    except Exception:  # noqa: BLE001
        cur = ()
    for name in cur + _NAMES:
        core = core.replace(name, "")
    core = _EN_FILLER_TAIL.sub("", _EN_FILLER_HEAD.sub("", core))
    return core in _DISMISS_EN


# ---------- 定稿模型 ----------

class Engine:
    name = "?"

    def __init__(self):
        self.rec = None
        self.last_used = 0.0
        self.load_ms = 0
        self.lock = threading.Lock()

    def available(self):
        raise NotImplementedError

    def _create(self, hotwords):
        raise NotImplementedError

    def load(self, hotwords):
        with self.lock:
            return self._load(hotwords)

    def _load(self, hotwords):
        if self.rec is None:
            t = time.monotonic()
            self.rec = self._create(hotwords)
            self.load_ms = int((time.monotonic() - t) * 1000)
            print(f"[asr] 已加载 {self.name}（{self.load_ms} ms）", flush=True)
        self.last_used = time.monotonic()
        return self.rec

    def unload(self):
        with self.lock:
            if self.rec is None:
                return
            self.rec = None
            print(f"[asr] 空闲，已卸载 {self.name}", flush=True)

    def decode(self, samples, hotwords):
        rec = self.load(hotwords)
        s = rec.create_stream()
        s.accept_waveform(SR, samples)
        rec.decode_stream(s)
        self.last_used = time.monotonic()
        r = s.result
        return {"text": _clean(r.text), "lang": _tag(getattr(r, "lang", "")), "emotion": _tag(getattr(r, "emotion", ""))}


def _tag(v):
    return re.sub(r"[<|>]", "", v or "").lower()


_t2s = None


def _clean(text):
    global _t2s
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    text = re.sub(r"^language\s+\w+\s*<asr_text>", "", text).strip()
    if text.lower() == "language":  # Qwen3-ASR 音频太长时偶尔只吐这个词
        return ""
    # Qwen3-ASR 有时输出繁体 → 统一简体（OpenCC 词库，Apache-2.0）
    if re.search(r"[\u4e00-\u9fff]", text):
        try:
            if _t2s is None:
                from opencc import OpenCC
                _t2s = OpenCC("t2s")
            text = _t2s.convert(text)
        except ImportError:
            pass
    return text


class Qwen3(Engine):
    name = "qwen3"

    def dir(self):
        return find_model_dir("sherpa-onnx-qwen3-asr")

    def available(self):
        d = self.dir()
        return bool(d) and os.path.exists(os.path.join(d, "encoder.int8.onnx"))

    def _create(self, hotwords):
        import sherpa_onnx
        d = self.dir()
        return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=os.path.join(d, "conv_frontend.onnx"),
            encoder=os.path.join(d, "encoder.int8.onnx"),
            decoder=os.path.join(d, "decoder.int8.onnx"),
            tokenizer=os.path.join(d, "tokenizer") if os.path.isdir(os.path.join(d, "tokenizer")) else d,
            num_threads=CFG["asr_threads"],
            hotwords=",".join(h for h in hotwords if "," not in h),
        )


class SenseVoice(Engine):
    name = "sensevoice"

    def dir(self):
        return find_model_dir("sherpa-onnx-sense-voice")

    def available(self):
        d = self.dir()
        return bool(d) and os.path.exists(os.path.join(d, "model.int8.onnx"))

    def _create(self, hotwords):
        import sherpa_onnx
        d = self.dir()
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(d, "model.int8.onnx"),
            tokens=os.path.join(d, "tokens.txt"),
            num_threads=CFG["asr_threads"],
            language="auto",
            use_itn=True,
        )


ENGINES = {"qwen3": Qwen3(), "sensevoice": SenseVoice()}


def engine():
    want = ENGINES.get(CFG["asr_engine"], ENGINES["qwen3"])
    if want.available():
        return want
    for e in ENGINES.values():
        if e.available():
            return e
    return None


# ---------- 听一句话 ----------

class Listener:
    def __init__(self):
        self.active = False
        self.source = ""
        self.deadline = 0.0
        self.speech_started = False
        self.started_at = 0.0
        self.apple_text = ""
        self.vad = None
        self.hotwords = load_hotwords()
        self.rules = load_corrections(self.hotwords)
        self.last_stop = 0.0
        self.thinking_since = 0.0
        self.last_final = None

    # VAD 很小（约 2MB），常驻
    def _vad(self):
        if self.vad is None:
            import sherpa_onnx
            c = sherpa_onnx.VadModelConfig()
            c.silero_vad.model = model_path("silero_vad", "silero_vad.onnx")
            c.silero_vad.threshold = CFG["vad_threshold"]
            c.silero_vad.min_silence_duration = CFG["end_silence_s"]
            c.silero_vad.min_speech_duration = 0.2
            c.silero_vad.max_speech_duration = CFG["max_utterance_s"]
            c.silero_vad.window_size = 512
            c.sample_rate = SR
            c.num_threads = 1
            self.vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=CFG["max_utterance_s"] + 10)
        return self.vad

    def sync_helper(self):
        hub.send_helper({"t": "listen", "on": self.active, "hotwords": self.hotwords[:100]})

    def begin(self, source, timeout=None, preroll_s=None):
        with _lock:
            if self.active:
                return
            self.active = True
            self.source = source
            self.speech_started = False
            self.apple_text = ""
            self.started_at = time.monotonic()
            self.deadline = self.started_at + (timeout or CFG["listen_timeout_s"])
            # 接话窗口（选「1 分钟内接着说，只认他的声音」）：记住窗口什么时候关，
            # 窗口里听到的不是他 / 是她自己的回声 / 没听出字，就接着在剩下的时间里听
            if source == "followup":
                if not getattr(self, "window_until", 0) or self.window_until < self.started_at:
                    self.window_until = self.deadline
            else:
                self.window_until = 0
            vad = self._vad()
            vad.reset()
            self.short = []
            # 追问窗口紧接着助手说完，环形缓冲里是她自己的声音，不能拿来当预录
            # 唤醒后：唤醒词刚说完，他还没开口，只带一点点（多了会把「…期五」的尾巴识别进去）
            pre_s = {"barge": CFG["barge_preroll_s"], "wake": CFG["wake_preroll_s"]}.get(source, CFG["preroll_s"])
            if preroll_s is not None:
                pre_s = preroll_s
            pre = hub.recent_audio(pre_s) if source != "followup" else []
            if len(pre):
                vad.accept_waveform(pre)
        self.sync_helper()
        hub.emit({"type": "listen", "on": True, "source": source})
        # 趁他说话的时候把定稿模型加载好
        e = engine()
        if e is not None and e.rec is None:
            threading.Thread(target=lambda: _safe(e.load, self.hotwords), name="asr-preload", daemon=True).start()

    def end(self, reason):
        with _lock:
            if not self.active:
                return False
            self.active = False
            self.held = False
        self.sync_helper()
        hub.emit({"type": "listen", "on": False, "reason": reason})
        return True

    def on_audio(self, chunk):
        segs = []
        with _lock:
            if not self.active:
                return
            vad = self._vad()
            vad.accept_waveform(chunk)
            if not self.speech_started and vad.is_speech_detected():
                self.speech_started = True
                hub.emit({"type": "user.speaking"})
            while not vad.empty():
                segs.append(np.array(vad.front.samples, dtype=np.float32))
                vad.pop()
            timed_out = not self.speech_started and not segs and time.monotonic() > self.deadline
        if segs and getattr(self, "held", False) and time.monotonic() - self.started_at < CFG["max_utterance_s"]:
            with _lock:  # 按住说话：停顿也先攒着，等松手
                self.short.extend(segs)
                self.deadline = time.monotonic() + 60
                self.speech_started = False
            segs = []
            timed_out = False
        if segs and sum(len(x) for x in segs) < 0.8 * SR and time.monotonic() - self.started_at < CFG["max_utterance_s"]:
            # 很短的一小段（「拨号，」后面停了一下）：先攒着，接着听，跟后面的话一起认
            with _lock:
                self.short.extend(segs)
                self.deadline = time.monotonic() + 1.2  # 1.2 秒内没接着说，就单独认这一小段
                self.speech_started = False
            segs = []
            timed_out = False
        if not segs and not self.active:
            return
        if segs:
            if self.end("utterance"):
                gap = [np.zeros(int(0.15 * SR), np.float32)] if self.short else []
                audio = np.concatenate(self.short + gap + segs)
                hub.set_state("thinking")
                self.thinking_since = time.monotonic()
                threading.Thread(target=self.finalize, args=(audio,), name="asr-final", daemon=True).start()
        elif timed_out:
            if self.short and self.end("utterance"):  # 只有那一小段，也认一下
                audio = np.concatenate(self.short)
                hub.set_state("thinking")
                self.thinking_since = time.monotonic()
                threading.Thread(target=self.finalize, args=(audio,), name="asr-final", daemon=True).start()
            elif self.end("timeout"):
                if self.source == "barge":
                    _barge().release("too-short")  # 暂停后没说出完整的话（咳嗽、敲桌子）：接着说
                else:
                    if self.source == "followup":
                        hub.emit({"type": "followup.end"})  # 窗口关了：提示音告诉他之后要重新叫名字
                    hub.set_state("idle")

    def _followup_continue(self, why):
        """接话窗口里这一句不算（不是他、回声、没听出字）：窗口还剩 1.5 秒以上就接着听。"""
        if self.source != "followup":
            return False
        left = getattr(self, "window_until", 0) - time.monotonic()
        if left < 1.5 or hub.state() not in ("thinking", "idle", "listening"):
            if left < 1.5:
                hub.emit({"type": "followup.end"})
            return False
        start_listening("followup", left)
        return True

    def finish_now(self):
        with _lock:
            if not self.active:
                return
            vad = self._vad()
            vad.flush()
            segs = []
            while not vad.empty():
                segs.append(np.array(vad.front.samples, dtype=np.float32))
                vad.pop()
            segs = self.short + segs
        if not self.end("release"):
            return
        if not segs:
            hub.set_state("idle")
            return
        hub.set_state("thinking")
        self.thinking_since = time.monotonic()
        threading.Thread(target=self.finalize, args=(np.concatenate(segs),), name="asr-final", daemon=True).start()

    def finalize(self, audio):
        t0 = time.monotonic()
        if self.source == "barge":
            _barge().judging()
        apple = self.apple_text
        out = {"text": "", "lang": "", "emotion": ""}
        # 声纹核对和识别同时跑（各自约 0.2s / 0.5s），省掉串行的那段
        spk, vres = _speaker(), {}
        vth = None
        if spk is not None:
            def _verify():
                try:
                    vres.update(spk.verify(audio) or {})
                except Exception:  # noqa: BLE001
                    print(f"[asr] 声纹核对出错:\n{traceback.format_exc()}", flush=True)
            vth = threading.Thread(target=_verify, name="asr-verify", daemon=True)
            vth.start()
        e = engine()
        if e is not None:
            try:
                out = e.decode(audio, self.hotwords)
            except Exception:  # noqa: BLE001
                print(f"[asr] 定稿失败，改用 Apple 结果:\n{traceback.format_exc()}", flush=True)
        if self.last_stop >= t0:
            return  # 定稿途中被「停」了
        text = out["text"] or apple
        text = apply_corrections(text, self.rules)
        ms = int((time.monotonic() - t0) * 1000)
        if not text and self.source == "barge":
            _barge().release("empty")
            return
        if not text and self._followup_continue("empty"):
            return
        if not text:
            hub.emit({"type": "user.empty", "dur": round(len(audio) / SR, 2)})
            # 很短的一段没听出字（咳嗽、半个词）→ 接着听，别直接结束
            if len(audio) / SR < 2.0 and self.source != "empty-retry":
                start_listening("empty-retry", 4)
            else:
                hub.set_state("idle")
            return
        if is_stop_phrase(text):
            hub.stop("voice")
            return
        if is_dismiss_phrase(text):
            print(f"[asr] 「{text}」→ 关掉对话窗口", flush=True)
            self.last_stop = time.monotonic()  # 不开追问窗口
            hub.stop("dismiss")
            hub.emit({"type": "dismiss", "text": text})
            return
        if self.source in ("followup", "empty-retry") and _barge().is_echo(text, _barge().spoken(4)):
            # 追问窗口里收到的其实是她自己刚说的话（回声消除关掉那一下、或扬声器尾音）：丢掉
            print("[asr] 追问窗口听到的是她自己的话，忽略", flush=True)
            _barge().log({"event": "followup_echo", "chars": len(text)})
            if not self._followup_continue("echo"):
                hub.set_state("idle")
            return
        if self.source == "followup" and CFG.get("followup_voice_gate", True) and vth is not None:
            vth.join(timeout=1.5)
            ok, why = _followup_voice_ok(vres)
            _barge().log({"event": "followup_voice", "score": vres.get("score"), "ok": ok, "why": why,
                          "dur": round(len(audio) / SR, 2)})
            if not ok:
                print(f"[asr] 接话窗口里听到的不像 用户（{vres.get('score')}），忽略，接着听", flush=True)
                hub.emit({"type": "user.rejected", "score": vres.get("score"), "source": "followup"})
                if not self._followup_continue("not-him"):
                    hub.set_state("idle")
                return
        if self.source == "barge":
            verdict = _barge().judge(text)
            _barge().log({"event": "judge", "verdict": verdict, "dur": round(len(audio) / SR, 2),
                          "chars": len(text)})  # 只记数字，不记他说的话
            if verdict != "real":
                print(f"[asr] 打断判断：{verdict}，接着说", flush=True)
                _barge().release(verdict)
                return
            _barge().commit("real")
            hub.stop("barge")  # 丢掉旧回答（清播放 + 页面取消还没写完的回答），下面把新问题发出去
        ev = {"type": "user.final", "text": text, "apple": apple, "lang": out.get("lang", ""),
              "emotion": out.get("emotion", ""), "engine": e.name if (e and out["text"]) else "apple",
              "ms": ms, "dur": round(len(audio) / SR, 2), "source": self.source}
        if vth is not None:
            vth.join(timeout=1.5)
            if "ok" in vres:
                ev["speaker_ok"], ev["speaker_score"] = bool(vres.get("ok")), vres.get("score")
                if CFG["speaker_gate"] and not ev["speaker_ok"]:
                    hub.emit({"type": "user.rejected", "text": text, "score": vres.get("score")})
                    hub.set_state("idle")
                    return
        self.last_final = ev
        print(f"[asr] {ev['engine']} {ms}ms {ev['dur']}s：{text}", flush=True)
        hub.emit(ev)


def _barge():
    from . import barge
    return barge


def _followup_voice_ok(vres):
    """接话窗口的声纹：短句、远场分数偏低，门槛放宽到 max(0.35, 档案门槛 − 0.30)；不到 0.8 秒的短句（「好」「对」）算通过。
    11-08：他真人接话 0.43–0.50（门槛原来 0.50，「那你查呗」被当成别人扔掉）；她自己的声音 ≤0.16，冒充 ≤0.12。"""
    if not vres or vres.get("reason") == "not_enrolled":
        return True, "no-profile"
    if vres.get("reason") == "too_short":
        return True, "short"
    thr = max(CFG.get("followup_voice_floor", 0.45), float(vres.get("threshold") or 0.65) - 0.30)
    return float(vres.get("score") or 0) >= thr, "score"


def _speaker():
    try:
        from . import speaker  # 唤醒+声纹线程的模块
        return speaker if hasattr(speaker, "verify") else None
    except ImportError:
        return None


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:  # noqa: BLE001
        print(f"[asr] 预加载失败:\n{traceback.format_exc()}", flush=True)


L = Listener()


# ---------- 对外 ----------

def start_listening(source="ui", timeout=None, preroll_s=None):
    hub.set_state("listening")
    L.begin(source, timeout, preroll_s)


def info():
    e = engine()
    return {"engine": e.name if e else None, "loaded": bool(e and e.rec), "load_ms": e.load_ms if e else 0,
            "listening": L.active, "hotwords": len(L.hotwords), "models_dir": MODELS,
            "available": [k for k, v in ENGINES.items() if v.available()]}


def _on_state(ev):
    s, prev = ev.get("state"), ev.get("prev")
    if s == "listening" and not L.active:
        L.begin("state")
    elif s != "listening" and L.active and s != "thinking":
        L.end("state:" + s)
    # 助手说完（不是被「停」打断）→ 留一个短窗口，直接接着说，不用再喊唤醒词
    if prev == "speaking" and s == "idle" and CFG["followup_s"] > 0 and time.monotonic() - L.last_stop > 1.5:
        # 等扬声器余音和房间回声散掉再开始听；没有回声消除时要等久一点，否则会把她自己的尾音当成他在说话
        delay = CFG["echo_tail_aec_s"] if hub.aec_on else CFG["echo_tail_s"]

        def go():
            if hub.state() == "idle" and time.monotonic() - L.last_stop > 1.5:
                start_listening("followup", CFG["followup_s"])
        threading.Timer(delay, go).start()


def _on_wake_hit(ev):
    # 刚听到唤醒词、声纹还没核对完：待机时先开始听，省掉核对的那 0.2 秒
    if hub.state() == "idle":
        start_listening("wake")


def _on_wake(ev):
    if not ev.get("speaker_ok", True):
        # 声纹没过：取消刚才抢先开的那次听（他还没开口的话）
        if L.active and L.source == "wake" and not L.speech_started:
            L.end("rejected")
            hub.set_state("idle")
        return
    # 还在「想」的时候又叫它，说明想问新问题了（页面收到新的 wake 会放弃上一个回答）
    if hub.state() in ("idle", "listening", "thinking"):
        # 第二条唤醒路（转文字认名字）是在他说完一句后才认出来的：「你好 Vesper，现在几点」里名字后面那段
        # 已经在录音缓冲里了，preroll_s 让识别从名字后面接着认，他不用再说一遍
        if ev.get("preroll_s") and not L.active:
            start_listening("wake", preroll_s=float(ev["preroll_s"]))
        else:
            start_listening("wake")


def _on_partial(ev):
    if L.active:
        L.apple_text = ev.get("text", "")
        hub.emit({"type": "user.partial", "text": apply_corrections(L.apple_text, L.rules)})


def _on_stop(ev):
    L.last_stop = time.monotonic()
    L.end("stop")


def _on_hotkey(ev):
    if ev.get("name") == "talk_hold":  # 按住说话：中间停顿不算说完
        L.held = True
        return
    if ev.get("name") == "talk_release":  # 松手了：不等停顿，马上认
        L.held = False
        L.finish_now()
        return
    if ev.get("name") == "talk":  # ⌥⌘J：不用唤醒词，直接说（再按一次取消）
        if _barge().held():
            # 正在「开口打断」的暂停里又按了说话键：就是要打断，丢掉旧回答，重新听
            _barge().commit("hotkey")
            hub.stop("barge")
            start_listening("hotkey")
            return
        if hub.state() in ("speaking", "thinking"):
            # 她还在说/想的时候按键 = 插话：先停掉她，再开始听
            hub.stop("barge")
            start_listening("hotkey")
            return
        if L.active and L.source in ("hotkey", "ui"):
            # 自己按键开的才算「再按一次取消」
            L.end("hotkey")
            hub.set_state("idle")
        elif L.active:
            # 追问窗口 / 唤醒后自动在听：按键 = 「我要说」，接着听，并按按键说话的时长重新计时
            L.source = "hotkey"
            L.deadline = time.monotonic() + CFG["listen_timeout_s"]
            L.sync_helper()
        else:
            start_listening("hotkey")


def _on_assistant(ev):
    L.thinking_since = 0.0


def _housekeeping():
    while True:
        time.sleep(5)
        now = time.monotonic()
        for e in ENGINES.values():
            if e.rec is not None and not L.active and now - e.last_used > CFG["asr_idle_unload_s"]:
                e.unload()
        # 页面没接（Deck 没开）时别一直卡在 thinking
        if hub.state() == "thinking" and L.thinking_since and now - L.thinking_since > 60:
            L.thinking_since = 0.0
            hub.set_state("idle")


def reload_words():
    L.hotwords = load_hotwords()
    L.rules = load_corrections(L.hotwords)
    for e in ENGINES.values():
        e.unload()  # Qwen3 热词在加载时写入
    return len(L.hotwords)


def _http(method, path, query, body):
    if path == "/asr/reload":
        return 200, json.dumps({"hotwords": reload_words()}).encode(), "application/json"
    if path == "/asr/last":
        return 200, json.dumps(L.last_final or {}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
    return 404, b"{}", "application/json"


def start():
    hub.subscribe_audio(L.on_audio, name="asr")
    hub.on("state", _on_state)
    hub.on("wake", _on_wake)
    hub.on("wake.hit", _on_wake_hit)
    hub.on("apple.partial", _on_partial)
    hub.on("stop", _on_stop)
    hub.on("hotkey", _on_hotkey)
    hub.on("assistant.delta", _on_assistant)
    hub.on("assistant.done", _on_assistant)
    hub.route("*", "/asr/", _http)
    threading.Thread(target=_housekeeping, name="asr-housekeeping", daemon=True).start()
    e = engine()
    print(f"[asr] 定稿模型：{e.name if e else '没找到（先运行 fetch_asr_models.sh），暂用 Apple 结果'}；热词 {len(L.hotwords)} 个", flush=True)
