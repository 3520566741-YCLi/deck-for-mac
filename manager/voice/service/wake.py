"""「你好Vesper / Hey Vesper」常驻唤醒 + 声纹门（sherpa-onnx KWS zipformer zh-en 3M + 3D-Speaker CAM++）。

流程：hub 送来的 16k 音频 → 自己的线程里跑 KWS（流式，每 100ms）→ 命中后再等 0.15s 收尾
→ 按 KWS 给的 token 时间戳切出唤醒词那一段 → CAM++ 声纹打分 → hub.emit(wake)。
KWS 故意调得偏灵敏一点，真正把关的是声纹：只有用户的声音才 speaker_ok=true。

由 server.py 启动时调用 start(hub)。也可以单独跑：python wake_cli.py live
"""
import json
import re
import queue
import threading
import time
from pathlib import Path

import numpy as np

try:
    from . import names, speaker, voicepaths as P
except ImportError:  # wake_cli.py 直接运行
    import names
    import speaker
    import voicepaths as P

SR = 16000
DEFAULTS = {
    "kws_threshold": 0.25,     # 越大越难触发；声纹兜底，所以 KWS 不用太严
    "kws_score": 1.5,          # 关键词加分（boost）
    "cooldown_s": 8.0,         # 唤醒成功后 8 秒内不重复唤醒
    "reject_cooldown_s": 1.5,  # 被拒后短暂冷却，防止一句话触发好几次
    "kws_threshold_en": 0.07,  # 带英文的唤醒词（Kira 都算）。11-06 sweep：0.15/2.0 命中 55%，0.07/3.0 58%，普通聊天都零误命中
    "kws_score_en": 3.0,
    "early_listen": True,      # 一听到唤醒词就先开始听（声纹在后台核对，没过再取消），省掉约 0.2 秒
    "soft_min_score": 0.35,
    # 英文唤醒：声纹按中文唤醒词录时，说英文唤醒词时文本相关那部分分数天然低（实测 0.14–0.42）。
    # 英文放宽到 0.22（中文冒充测试最高 0.23，英文在家里被别人喊中的机会很小）；分数都记日志，之后再调
    "soft_min_score_en": 0.22,    # 声纹没过门槛但分数 ≥ 这个也放行：开回声消除时本人只有 0.45–0.65，冒充最高 0.23
    # 新档案（v2）也留一点余量（11-07）：门槛被下限夹到 0.55，可他自己 8 遍录音留一法就有 0.50 / 0.54 两遍过不了，
    # 隔一点距离、换个音量更常落在 0.45–0.55，结果是「叫了它一下又自己取消」。放到 门槛 − 0.10，但不低于下面的线；
    # 冒充者：11-07 用 32 种 macOS 合成声音中英文各说一遍，最高 0.356（Daniel，男声），余量约 0.09
    "near_margin": 0.13,
    "near_floor": 0.42,     # 11-08：他真人实测 0.42–0.68（03:07 那次 0.456 差点被拒），冒充最高 0.356
    "near_floor_en": 0.42,
    "max_seg_s": 1.8,          # 声纹只看检测点前最后 1.8 秒（唤醒词本身约 1–1.7 秒）
    "allow_unenrolled": True,  # 还没录声纹时也能唤醒（事件里 enrolled=false，Deck 可以提示去录入）
    "log_scores": True,        # 只记分数，不存音频
}


def settings():
    f = P.HOME / "wake.json"
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(f.read_text()))
    except Exception:
        pass
    return s


# 名字的读法。Kira 不一定在 CMU 词典里，自己写：英式 K IY1 R AH0（Kee-ra）、美式 K IH1 R AH0（Kih-ra），
# 再加一个中文口音读法 q í l ā（说「嘿 Kira」时常这么念）。每种都写一行，命中哪个都算，声纹兜底。
# FRIDAY 留着给 wake_cli 等旧测试用。
# 候选名字的中文念法（说「嘿 Luna」时常带中文口音），按拼音再写一种读法。
EXTRA_PRONS = {
    "KIRA": [["K", "IY1", "R", "AH0"], ["K", "IH1", "R", "AH0"], ["K", "IY1", "R", "AA0"], ["q", "í", "l", "ā"]],
    "FRIDAY": [["F", "R", "AY1", "D", "EY2"], ["F", "R", "AY1", "D", "IY0"]],
}
NAME_ZH = {"KIRA": "琪拉", "LUNA": "露娜", "NOVA": "诺娃", "MIA": "米娅", "LUCY": "露西", "COCO": "可可",
           "MOMO": "莫莫", "LILY": "莉莉", "JARVIS": "贾维斯", "ELLA": "艾拉", "LUMI": "露米", "NANA": "娜娜",
           "MILO": "米洛", "LULU": "露露",
           # 第二批：少见、有辨识度的名字
           "JUNO": "朱诺", "LYRA": "莱拉", "VEGA": "维加", "REMY": "雷米", "SUKI": "苏琪", "MOCHI": "莫奇",
           "ZURI": "祖丽", "NEMO": "尼莫", "KIKO": "琪可", "ORLA": "奥拉", "KAIA": "凯娅", "PIXIE": "皮克西",
           "ZELLA": "泽拉", "TALI": "塔莉", "NIKO": "尼可", "ROMI": "罗米", "DAKOTA": "达科塔", "ATLAS": "阿特拉斯",
           "VESPER": "维斯帕"}


def _token_variants(phrase, phones, limit=8, name=None, hint=None):
    """英文词可能有几种读法（Kira：K IY1 R AH0 / K IH1 R AH0 / q í l ā），每种组合各写一行，命中哪个都算。
    11-08：名字可以由用户随便起。词典里没有的名字按规则猜读法（names.rules_g2p），用户填了读法提示的排最前。"""
    import itertools
    import re
    name = P.NAME if name is None else name
    hint = getattr(P, "NAME_HINT", "") if hint is None else hint
    parts = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z']+", phrase)
    choices = []
    for part in parts:
        is_name = part.upper() == name.upper() or part == name
        if re.match(r"[\u4e00-\u9fff]", part):
            # 中文前缀（你好）和中文名字（你好小美）粘在一起：把名字单独拆出来，好加读法提示
            if name in part and CJK_NAME(name):
                pre = part[: part.index(name)]
                if pre:
                    choices.append([_tokens(pre)])
                nm = [_tokens(name)] + [h for h in names.hint_prons(hint) if h != _tokens(name)]
                choices.append(nm[:3])
                continue
            toks = _tokens(part)
            if toks is None:
                return []
            choices.append([toks])
        else:
            extra = list(EXTRA_PRONS.get(part.upper(), []))
            if is_name:
                extra = [h for h in names.hint_prons(hint) if h not in extra] + extra
            zh = NAME_ZH.get(part.upper())
            if zh and part.upper() not in EXTRA_PRONS:
                extra.append(_tokens(zh))
            prons = list(extra)  # 自己写的读法排前面
            for x in (phones or {}).get(part.upper()) or []:
                if x not in prons:
                    prons.append(x)
            if is_name and not (phones or {}).get(part.upper()):
                for x in names.rules_g2p(part, 3):   # 词典里没有的名字：按拼写猜
                    if x not in prons:
                        prons.append(x)
                extra = extra or prons[:1]
            if not prons:
                return []
            # 自己写的几种全要，再加词典里的第一种（KIRA 在 en.phone 里是 K IH1 R AA0）
            choices.append(prons[:len(extra) + 1] if extra else prons[:2])
    out = []
    for combo in itertools.product(*choices):
        toks = [t for toks in combo for t in toks]
        if toks not in out:          # 读法提示和内置中文念法一样时（Mochi / 莫奇）别写两行
            out.append(toks)
        if len(out) >= limit:
            break
    return out


def CJK_NAME(name):
    return bool(re.search(r"[\u4e00-\u9fff]", name or ""))


def _tokens(phrase, phones=None):
    """中文 → 声母 + 带调韵母（和官方 text2token 的 ppinyin 一致，但不用装 sentencepiece）；
    英文单词 → CMU 音素（phones 来自模型目录的 en.phone）。有英文词查不到时返回 None。"""
    import re
    from pypinyin import pinyin
    from pypinyin.contrib.tone_convert import to_finals_tone, to_initials
    out = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z']+", phrase):
        if re.match(r"[\u4e00-\u9fff]", part):
            for (py,) in pinyin(part):
                out += [x for x in (to_initials(py, strict=False), to_finals_tone(py, strict=False)) if x] or [py]
        else:
            ph = (phones or {}).get(part.upper())
            if not ph:
                return None
            out += ph
    return out


def build_keywords(words, main, threshold, score, threshold_en=None, score_en=None, name=None, hint=None):
    """把叫法列表变成 sherpa 关键词文件的内容。返回 (内容, 主唤醒词有几种读法)。"""
    vocab = {ln.split()[0] for ln in (P.KWS_DIR / "tokens.txt").read_text(encoding="utf-8").splitlines() if ln.strip()}
    body = ""
    phones = names.lexicon()
    name = P.NAME if name is None else name
    main_n = 0
    for w in words:
        en = re.search(r"[A-Za-z]", w) is not None
        variants = _token_variants(w, phones, name=name, hint=hint) if (en or CJK_NAME(name)) else [_tokens(w)]
        if not variants or variants[0] is None:
            print(f"[wake] 「{w}」里有词不在英文音素表里，跳过", flush=True)
            continue
        # 标签里不能有空格：sherpa 会把空格后的部分当成 token 去查词表，查不到就直接退出进程（11-05 「@hey friday」踩过）
        label = re.sub(r"\s+", "_", w.strip())
        sc = score_en if en and score_en is not None else score
        th = threshold_en if en and threshold_en is not None else threshold
        n = 0
        for toks in variants:
            missing = [t for t in toks if t not in vocab]
            if missing:
                print(f"[wake] 「{w}」的音素 {missing} 不在词表里，跳过这种读法", flush=True)
                continue
            body += " ".join(toks) + f" :{sc} #{th} @{label}\n"
            n += 1
        if w == main:
            main_n = n
    return body, main_n


def ensure_keywords(threshold, score, threshold_en=None, score_en=None):
    """生成 keywords-wake.txt，例：n ǐ h ǎo K IY1 R AH0 :2.0 #0.15 @你好Kira"""
    out = P.KWS_DIR / "keywords-wake.txt"
    body, main_n = build_keywords(getattr(P, "WAKE_WORDS", [P.WAKE_WORD]), P.WAKE_WORD, threshold, score, threshold_en, score_en)
    if main_n == 0:
        raise FileNotFoundError(f"主唤醒词「{P.WAKE_WORD}」没有一种读法在模型词表里")
    if not out.exists() or out.read_text(encoding="utf-8") != body:
        out.write_text(body, encoding="utf-8")
    return str(out)


_spotter = None
_spot_lock = threading.Lock()


def make_spotter(keywords_file):
    import sherpa_onnx
    s = settings()
    return sherpa_onnx.KeywordSpotter(
        tokens=str(P.KWS_DIR / "tokens.txt"),
        encoder=P.kws_file("encoder*chunk-16*int8.onnx", "encoder*int8.onnx", "encoder*.onnx"),
        decoder=P.kws_file("decoder*chunk-16*.onnx", "decoder*.onnx"),
        joiner=P.kws_file("joiner*chunk-16*int8.onnx", "joiner*int8.onnx", "joiner*.onnx"),
        keywords_file=keywords_file,
        num_threads=1, max_active_paths=4, num_trailing_blanks=1,
        keywords_score=s["kws_score"], keywords_threshold=s["kws_threshold"], provider="cpu")


def spotter():
    global _spotter
    if _spotter is None:
        s = settings()
        _spotter = make_spotter(ensure_keywords(s["kws_threshold"], s["kws_score"], s["kws_threshold_en"], s["kws_score_en"]))
    return _spotter


def check_name(name, hint=""):
    """改名前先看唤醒模型念不念得出来。返回 (主唤醒词的读法数, 示例读法)。不改任何东西。"""
    s = settings()
    zh, en = P.phrases(name)
    body, n = build_keywords(zh[:1] + en[:1], "你好" + name, s["kws_threshold"], s["kws_score"],
                             s["kws_threshold_en"], s["kws_score_en"], name=name, hint=hint)
    first = body.splitlines()[0].split(" :")[0] if body else ""
    return n, first


def rename(name, hint=""):
    """改名：写文件 → 重建关键词和唤醒模型（当场生效，不用重启服务）→ 第二条路换成新名字。
    新模型建不起来就退回旧名字，绝不让唤醒整个挂掉。"""
    global _spotter
    old = (P.NAME, getattr(P, "NAME_HINT", ""))
    P.save_name(name, hint)
    try:
        with _spot_lock:
            _spotter = None
            sp = spotter()
            if engine is not None:
                engine.kws = sp
                engine.stream = sp.create_stream()
                engine._since_reset = 0
    except Exception:
        P.save_name(*old)
        with _spot_lock:
            _spotter = None
            sp = spotter()
            if engine is not None:
                engine.kws = sp
                engine.stream = sp.create_stream()
        raise
    _reset_matcher()
    return P.NAME


def spot_offline(samples):
    """录入时用：这段录音里说出了哪个唤醒词。返回 (命中的唤醒词标签或 None, 起止秒)。"""
    kws = spotter()
    samples = np.concatenate([np.asarray(samples, np.float32), np.zeros(int(SR * 0.6), np.float32)])
    with _spot_lock:
        st = kws.create_stream()
        st.accept_waveform(SR, samples)
        st.input_finished()
        while kws.is_ready(st):
            kws.decode_stream(st)
            kw = kws.get_result(st)
            if kw:
                ts = list(kws.timestamps(st))
                return kw, (ts[0], ts[-1]) if ts else None
    return None, None


def trim_speech(seg, pad=0.12):
    """按短时能量去掉首尾静音（20ms 帧，门限 = 底噪 × 3）。"""
    f = int(SR * 0.02)
    n = len(seg) // f
    if n < 5:
        return seg
    rms = np.sqrt(np.mean(seg[: n * f].reshape(n, f) ** 2, axis=1) + 1e-12)
    floor = np.percentile(rms, 15)
    on = np.where(rms > max(floor * 3, 0.004))[0]
    if len(on) == 0:
        return seg
    a = max(0, on[0] * f - int(pad * SR))
    b = min(len(seg), (on[-1] + 1) * f + int(pad * SR))
    return seg[a:b]


class Ring:
    """最近几秒的音频，按“从开机起第几个采样”取片段。"""

    def __init__(self, seconds=6):
        self.buf = np.zeros(SR * seconds, np.float32)
        self.total = 0

    def write(self, x):
        n, L = len(x), len(self.buf)
        if n >= L:
            self.buf[:] = x[-L:]
        else:
            i = self.total % L
            k = min(n, L - i)
            self.buf[i:i + k] = x[:k]
            self.buf[: n - k] = x[k:]
        self.total += n

    def slice(self, a, b):
        L = len(self.buf)
        a = max(a, self.total - L, 0)
        b = min(b, self.total)
        if b <= a:
            return np.zeros(0, np.float32)
        idx = np.arange(a, b) % L
        return self.buf[idx]


class WakeEngine:
    def __init__(self, emit, get_state=lambda: "idle", log_path=None):
        self.emit = emit
        self.get_state = get_state
        self.cfg = settings()
        self.kws = spotter()
        self.stream = self.kws.create_stream()
        self.ring = Ring()
        self.q = queue.Queue(maxsize=300)   # 约 30 秒积压上限，超了丢最旧的
        self.cool_until = 0.0
        self.pending = None
        self.paused_by = set()              # 录入页面会暂停唤醒
        self.log_path = Path(log_path or P.WAKE_LOG)
        self.last = None                    # 最近一次唤醒/拒绝（给录入页「让朋友试试」显示）
        # 收音体检（11-06：设备切换后 DeckVoice 没送音频，状态全是正常的，只读查不出来）
        self.audio = {"frames": 0, "last_t": 0.0, "rms": 0.0, "peak_5s": 0.0, "_win": []}
        self.last_processed = 0.0           # 唤醒线程最近一次处理完音频的时间（自检看它有没有卡住）
        self.last_kws_total = -10 ** 12     # 最近一次 KWS 命中时 ring.total（第二条路据此避免重复）
        self.asr_path = AsrWake(self) if self.cfg.get("asr_wake", True) else None
        self.health = {"ok": True, "problems": [], "t": None}
        self.tap = None                     # 唤醒测试（Deck 设置 → 助手）：结果交给它，不真的唤醒
        self._t = threading.Thread(target=self._run, name="friday-wake", daemon=True)
        self._t.start()
        threading.Thread(target=self._health_loop, name="wake-health", daemon=True).start()

    # hub.subscribe_audio 的回调：只入队，绝不阻塞 hub
    def feed(self, samples):
        try:
            x = np.asarray(samples, np.float32)
            now = time.time()
            a = self.audio
            a["frames"] += len(x)
            a["last_t"] = now
            r = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
            a["rms"] = r
            a["_win"] = [(t, v) for t, v in a["_win"] if now - t < 5] + [(now, r)]
            a["peak_5s"] = max(v for _, v in a["_win"])
        except Exception:
            pass
        try:
            self.q.put_nowait(np.asarray(samples, np.float32))
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(np.asarray(samples, np.float32))
            except queue.Empty:
                pass

    def audio_info(self):
        a = self.audio
        age = time.time() - a["last_t"] if a["last_t"] else None
        return {"samples_total": a["frames"], "last_audio_age_s": None if age is None else round(age, 2),
                "rms": round(a["rms"], 5), "rms_peak_5s": round(a["peak_5s"], 5),
                "ok": age is not None and age < 2 and a["peak_5s"] > 1e-4}

    def pause(self, who):
        self.paused_by.add(who)

    def resume(self, who):
        self.paused_by.discard(who)

    def _run(self):
        while True:
            x = self.q.get()
            try:
                self._process(x)
            except Exception as e:  # 一帧出错不能让唤醒整个挂掉
                self._log({"event": "error", "err": repr(e)})
                time.sleep(0.05)

    def _process(self, x):
        self.ring.write(x)
        base_since_reset = getattr(self, "_since_reset", 0) + len(x)
        self._since_reset = base_since_reset
        with _spot_lock:
            self.stream.accept_waveform(SR, x)
            while self.kws.is_ready(self.stream):
                self.kws.decode_stream(self.stream)
                kw = self.kws.get_result(self.stream)
                if kw:
                    ts = list(self.kws.timestamps(self.stream))
                    self.kws.reset_stream(self.stream)
                    self.last_kws_total = self.ring.total
                    self._on_keyword(kw, ts)
                    self._since_reset = 0
        if self.pending and self.ring.total >= self.pending["until"]:
            p, self.pending = self.pending, None
            self._judge(p)
        if self.asr_path is not None:
            self.asr_path.feed(x)
        self.last_processed = time.time()

    def _gate_ok(self):
        """现在能不能唤醒（两条路共用）：录入页暂停、她在说话、冷却中都不行。"""
        if self.paused_by or self.get_state() == "speaking" or time.time() < self.cool_until:
            return False
        return True

    def _segment_start(self, ts):
        """唤醒词开始于第几个采样。sherpa-onnx 2.0.1 的 KWS 实测不给时间戳（列表为空），这时取检测点前
        2.4 秒，再由 trim_speech 按能量去掉首尾静音（实测切出约 1.0 秒，声纹分数正常）。
        以后版本若给了时间戳，就按时间戳切（从流开始或从上次 reset 算，两种都试）。"""
        end_abs = self.ring.total
        if ts:
            for base in (end_abs, self._since_reset):
                lag = base / SR - ts[-1]          # 最后一个 token 到现在过了多久
                dur = ts[-1] - ts[0]
                if 0 <= lag <= 1.5 and 0.3 <= dur <= 3.0:
                    return end_abs - int((lag + dur + 0.3) * SR), "ts"
        return end_abs - int(2.4 * SR), "fallback"

    def _on_keyword(self, kw, ts):
        now = time.time()
        state = self.get_state()
        if self.paused_by:
            self._log({"event": "ignored", "why": "paused:" + ",".join(sorted(self.paused_by))})
            return
        if state == "speaking":
            self._log({"event": "ignored", "why": "speaking"})
            return
        if now < self.cool_until:
            self._log({"event": "ignored", "why": "cooldown"})
            return
        start, how = self._segment_start(ts)
        self.pending = {"kw": kw, "start": start, "until": self.ring.total + int(0.15 * SR), "how": how, "t": now}
        if self.cfg.get("early_listen", True) and state == "idle" and self.tap is None:
            # 先开始听（提示音、窗口马上出来），声纹 0.2 秒后核对；没过会再发 wake(speaker_ok=False) 取消
            self.emit({"type": "wake.hit", "kws": (kw or P.WAKE_WORD).replace("_", " ")})

    def _soft_gate(self, prof, lang):
        """放宽线只给旧档案兜底：
        - 旧档案（11-05 用内置麦克风直录，没经过 DeckVoice）：中英文都用 soft_min_score(_en)；
        - 新档案（v2，经 DeckVoice 录）但没录英文：英文仍用 soft_min_score_en；
        - 新档案且这门语言录过：门槛 − near_margin，不低于 near_floor(_en)（11-07：原来不放宽，他本人常被拒）。"""
        if prof.version < 2 or prof.meta.get("chain") != "deckvoice":
            return self.cfg["soft_min_score_en"] if lang == "en" else self.cfg["soft_min_score"]
        if lang == "en" and not prof.has("en"):
            return self.cfg["soft_min_score_en"]
        thr = prof.threshold_for(lang)
        if thr is None:
            return None
        floor = self.cfg["near_floor_en"] if lang == "en" else self.cfg["near_floor"]
        return round(max(thr - self.cfg["near_margin"], floor), 3)

    def _judge(self, p):
        seg = p["seg"] if "seg" in p else trim_speech(self.ring.slice(p["start"], p["until"]))
        # 唤醒词就在检测点前面（录入时只有 1.0–1.7 秒）。有环境声时能量去静音会留下 2 秒多，混进去的噪声
        # 和前面的话会把声纹分拉低（11-06：≤1 秒的片段 0.55–0.64，2.3–2.55 秒的只有 0.27–0.40），所以只留最后 MAX_SEG_S
        cap = int(self.cfg.get("max_seg_s", 1.8) * SR)
        if len(seg) > cap:
            seg = seg[-cap:]
        prof = speaker.profile()
        info = {"kw": p["kw"], "seg_s": round(len(seg) / SR, 2), "how": p["how"],
                "rms": round(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0, 4)}
        if not prof.enrolled:
            ok = bool(self.cfg["allow_unenrolled"])
            info.update(score=None, enrolled=False)
        elif len(seg) < SR * 0.5:
            ok = False
            info.update(score=0.0, reason="too_short", enrolled=True)
        else:
            e = speaker.embed(seg)
            best = None
            # 第二条路只说了名字（没「你好 / Hey」）时中英文两组都比，取更像的那组
            for lang in p.get("langs") or [speaker.lang_of(p["kw"])]:
                r = speaker.check(e, lang)
                soft = self._soft_gate(prof, lang)
                ok = r["ok"] or (soft is not None and r["score"] >= soft)
                if best is None or (ok, r["score"]) > (best[0], best[1]["score"]):
                    best = (ok, r, soft, lang)
            ok, r, soft, lang = best
            info.update(score=r["score"], td=r["td"], ti=r["ti"], threshold=r["threshold"], enrolled=True,
                        strict_ok=r["ok"], lang=lang, pool=r.get("pool"), soft=soft)
            if r["ok"] and self.tap is None and prof.maybe_adapt(e, r["score"], lang):  # 测试时绝不补录（可能是别人在试）
                info["adapted"] = True
        info.update(p.get("extra") or {})
        info["latency_ms"] = int((time.time() - p["t"]) * 1000)
        tap = self.tap
        if tap is not None:
            # 测试中：只记结果，不唤醒、不补录声纹。冷却短一点，下一遍马上能测
            self.cool_until = time.time() + 0.8
            self._log(dict(info, ok=ok, event="test"))
            try:
                tap(dict(info, ok=ok, path=p.get("path") or "kws"))
            except Exception:  # noqa: BLE001
                pass
            return
        self.cool_until = time.time() + (self.cfg["cooldown_s"] if ok else self.cfg["reject_cooldown_s"])
        ev = {"type": "wake", "speaker_ok": ok, "score": info.get("score"), "enrolled": info["enrolled"],
              "kws": (p["kw"] or P.WAKE_WORD).replace("_", " ")}
        if not ok and "reason" in info:
            ev["reason"] = info["reason"]
        if ok and p.get("preroll_s"):
            ev["preroll_s"] = p["preroll_s"]   # 第二条路：名字后面已经说了的话，让识别从这里接着认
        if p.get("path"):
            ev["path"] = p["path"]
        self.last = dict(info, ok=ok, t=time.strftime("%H:%M:%S"))
        self._log(dict(info, event="wake" if ok else "reject"))
        self.emit(ev)

    # ---------- 自检（11-08：叫了四次都没反应，链路哪一段坏了要能自己发现，并在 Deck 上提醒） ----------
    def self_test(self):
        """拿用户自己录的唤醒词跑一遍两条识别路，再看音频和唤醒线程是不是活着。返回 health。"""
        return self._publish(self._check())

    def _check(self):
        problems = []
        a = self.audio_info()
        hub_mod = _hub_mod()
        mic_on = bool(getattr(hub_mod, "_mic_on", True)) if hub_mod else True
        helper = bool(getattr(hub_mod, "_helper_connected", True)) if hub_mod else True
        if not helper:
            problems.append("DeckVoice 没连上语音服务")
        elif mic_on and not a["ok"]:
            problems.append("麦克风开着但没有声音进来")
        if a["ok"] and self.last_processed and time.time() - self.last_processed > 5:
            problems.append("唤醒检测卡住了（声音在进来，但没在处理）")
        clip = _selftest_clip()
        prof = speaker.profile()
        # 改过名字、声纹还是按旧名字录的：那段录音里说的是旧名字，拿它测新名字必然「认不出」，先不测（设置里会提示重录）
        if clip is not None and prof.enrolled and prof.meta.get("wake_word", "你好星期五") != P.WAKE_WORD:
            clip = None
        if clip is not None:
            kw, _ = spot_offline(clip)
            asr_hit = self.asr_path.match_clip(clip) if self.asr_path is not None else None
            if not kw and not asr_hit:
                problems.append(f"拿你的录音自测，两条唤醒路都没认出「{P.NAME}」")
        health = {"ok": not problems, "problems": problems, "t": time.strftime("%H:%M:%S"),
                  "asr_path": self.asr_path is not None and self.asr_path.ready}
        return health

    def _publish(self, health):
        changed = health["ok"] != self.health.get("ok")
        self.health = health
        if changed or not health["ok"]:     # 坏着的时候每次都发：后打开的 Deck 也能看到
            self.emit({"type": "voice.health", **health})
            self._log({"event": "health", **health})
        return health

    def _health_loop(self):
        time.sleep(self.cfg.get("health_first_s", 60))
        while True:
            try:
                h = self._check()
                if not h["ok"]:
                    time.sleep(15)          # 换设备、重开麦克风这类几秒钟的事不算，15 秒后再确认一次
                    h = self._check()
                self._publish(h)
            except Exception as e:  # noqa: BLE001
                self._log({"event": "error", "where": "health", "err": repr(e)})
            time.sleep(self.cfg.get("health_every_s", 600))

    def _log(self, d):
        if not self.cfg.get("log_scores", True):
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            d = dict(d, ts=time.strftime("%Y-%m-%d %H:%M:%S"))
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            if self.log_path.stat().st_size > 1_000_000:   # 只留最近 2000 行
                lines = self.log_path.read_text(encoding="utf-8").splitlines()[-2000:]
                self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass


# ---------- 第二条唤醒路：短句转文字，找「Vesper」（11-08） ----------
# 为什么要：KWS 模型只有 3M 参数，合成声音测命中率才 55–58%；用户 真人说时常常压根没认出来——
# 认不出就连一行日志都没有，表现就是「叫了好多遍一点反应都没有」。SenseVoice（226 MB，int8）把他 13 遍
# 录音全认出来了（WSPER / WESPER / VISSPER / 威PER…），一句 2 秒的话 40–60 ms。
# 所以：VAD 切出待机时的短句 → SenseVoice 转文字 → 名字在句子开头（前面最多是「你好 / Hey / 早上好」）
# → 声纹核对（和 KWS 那条路同一个门槛）。说「我刚跟 Vesper 聊天」这种名字在中间的不算。

# 英文写法：V/W/B/F 开头、PER 结尾、中间最多 3 个字母（VESPER、WSPER、VISSPER、WEPER、VPER）
# 11-08 又见到「WER」「WBER」：P 被吞掉的，只认 V/W 开头的这几种短写法（WATER、BETTER 这类常用词不会对上）
_NAME_EN = re.compile(r"(?<![A-Z])([VWBF][A-Z]{0,3}PER|[VW]E?S?B?ER)(?![A-Z])")
# 这些也长那样，但都是常用词，不算
_NAME_EN_NOT = {"WHISPER", "WHIMPER", "WRAPPER", "WIPER", "VIPER", "WHOPPER", "WHIPPER", "WEEPER", "BUMPER",
                "BEEPER", "FLIPPER", "FLOPPER", "BOPPER", "BIPPER", "WAPPER", "FLAPPER", "BOOPER", "VAPER",
                "WEBPER", "BAPER"}
# 中文写法：维斯帕、威斯帕、薇丝珀、威PER…
_NAME_ZH = re.compile(r"[维威薇韦唯微魏伟][斯丝思司]?(?:帕|珀|柏|泊|PER|BER|ER)")
_CJK = re.compile(r"[一-鿿]")
_GREET = re.compile(r"(你好|您好|嘿|嗨|哈喽|喂|早上好|晚上好|在吗|HEY|HI\b|HELLO|OK\b|OKAY|GOOD)")


_matcher = None
_matcher_lock = threading.Lock()


def _reset_matcher():
    global _matcher
    with _matcher_lock:
        _matcher = None


def matcher():
    """当前名字的认名器（名字 + 读法提示 + 录入时学到的写法）。改名或录完声纹后会重建。"""
    global _matcher
    with _matcher_lock:
        if _matcher is None or _matcher.name != P.NAME:
            _matcher = names.Matcher(P.NAME, getattr(P, "NAME_HINT", ""), names.load_aliases(P.NAME),
                                     min_sim=settings().get("name_match_min", 0.8))
        return _matcher


def _match_vesper(t):
    """11-08 按用户真人录音调好的 Vesper 专用写法，名字还是 Vesper 时继续用（不让他的唤醒率变差）。"""
    for m in list(_NAME_ZH.finditer(t)) + [m for m in _NAME_EN.finditer(t) if m.group(1) not in _NAME_EN_NOT]:
        before, after = t[:m.start()].strip(), t[m.end():].strip()
        words = [w for w in re.split(r"\s+", re.sub(r"[一-鿿]", " ", before)) if w]
        cjk = len(_CJK.findall(before))
        # 名字前面只能是很短的招呼：最多 4 个汉字、最多 2 个英文词（你好 / 早上好 / Hey / Good morning / 嘿EY）
        if cjk <= 4 and len(words) <= 2 and cjk + len(words) <= 4:
            return m.end(), before, after
    return None


def match_name(text):
    """SenseVoice 的结果里有没有在叫她。返回 (名字结束在第几个字符, 名字前面的话, 后面的话) 或 None。"""
    t = (text or "").upper()
    t = re.sub(r"[，。！？、,.!?;；:：\"'“”‘’]", " ", t)
    if P.NAME.upper() == "VESPER":
        hit = _match_vesper(t)
        if hit:
            return hit
    try:
        return matcher().find(t)
    except Exception:  # noqa: BLE001  认名器出错不能影响第一条路
        return None


class AsrWake:
    """在唤醒线程里跑：喂音频 → VAD 断句 → 待机时的短句交给 SenseVoice。模型缺了就安静地不启用。"""

    def __init__(self, eng):
        self.eng = eng
        self.ready = False
        self.rec = None
        self.vad = None
        self.base = 0                 # VAD 创建时 ring.total（VAD 段的 start 是从这里数的采样）
        self.lock = threading.Lock()  # 自检线程也会用识别器
        try:
            self._load()
            self.ready = True
        except Exception as e:  # noqa: BLE001
            print(f"[wake] 第二条唤醒路没启用：{e!r}", flush=True)

    def _load(self):
        import glob
        import sherpa_onnx
        d = sorted(glob.glob(str(P.MODELS / "sherpa-onnx-sense-voice*")))
        if not d or not P.SILERO_VAD.exists():
            raise FileNotFoundError("没有 SenseVoice 或 Silero VAD 模型")
        d = d[-1]
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=d + "/model.int8.onnx", tokens=d + "/tokens.txt", num_threads=1, language="auto", use_itn=True)
        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = str(P.SILERO_VAD)
        c.silero_vad.threshold = 0.5
        c.silero_vad.min_silence_duration = 0.3   # 说完停 0.3 秒就断句，唤醒要快
        c.silero_vad.min_speech_duration = 0.25
        c.silero_vad.max_speech_duration = 8
        c.silero_vad.window_size = 512
        c.sample_rate = SR
        c.num_threads = 1
        self.vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=20)
        self.base = self.eng.ring.total

    def transcribe(self, x):
        with self.lock:
            st = self.rec.create_stream()
            st.accept_waveform(SR, np.asarray(x, np.float32))
            self.rec.decode_stream(st)
            r = st.result
            return r.text, list(r.tokens), list(r.timestamps)

    def match_clip(self, x):
        return match_name(self.transcribe(x)[0]) is not None

    def feed(self, x):
        if not self.ready:
            return
        self.vad.accept_waveform(x)
        while not self.vad.empty():
            seg = self.vad.front
            samples, start = np.array(seg.samples, dtype=np.float32), int(seg.start)
            self.vad.pop()
            try:
                self._segment(samples, self.base + start)
            except Exception as e:  # noqa: BLE001
                self.eng._log({"event": "error", "where": "asr_wake", "err": repr(e)})

    def _segment(self, seg, start_abs):
        eng = self.eng
        dur = len(seg) / SR
        if not (0.4 <= dur <= 6.0) or eng.get_state() != "idle" or not eng._gate_ok():
            return
        if eng.last_kws_total >= start_abs - int(0.3 * SR):
            return  # 这句 KWS 已经认出来了，那条路在管
        t0 = time.time()
        # 长句（「你好 Vesper，帮我把磁盘清一下」）整句转文字时名字常被吞掉（11-08 实测只剩「你好把磁盘…」），
        # 只转开头 2.4 秒就认得出来
        head = seg if dur <= 3.0 else seg[: int(2.4 * SR)]
        text, toks, ts = self.transcribe(head)
        hit = match_name(text)
        if not hit and _GREET.match((text or "").upper().strip()) and len(head) > int(1.4 * SR):
            # 「你好…」开头却没名字：名字常被后面的话盖掉（11-08「你好现在几点了」），只听开头 1.7 秒再认一次
            head = seg[: int(1.7 * SR)]
            t2, k2, s2 = self.transcribe(head)
            if match_name(t2):
                text, toks, ts = t2, k2, s2
                hit = match_name(t2)
        if not hit:
            # 以招呼开头的短句却没认出名字：多半是他在叫、但名字被认成了别的。只记文字（不存音频），下次好对症调
            if dur <= 3.0 and _GREET.match((text or "").upper().strip()):
                eng._log({"event": "heard", "path": "asr", "text": text[:30], "seg_s": round(dur, 2)})
            return
        _, before, after = hit
        name_end = _name_end_s(toks, ts, len(head) / SR)
        vseg = trim_speech(seg[: int(min(dur, name_end + 0.2) * SR)])
        cap = int(eng.cfg.get("max_seg_s", 1.8) * SR)
        if len(vseg) > cap:
            vseg = vseg[-cap:]
        zh = bool(_CJK.search(before))
        langs = ["zh"] if zh else (["en"] if before else ["zh", "en"])
        rest = re.sub(r"[\s，。！？、,.!?]", "", after)
        pre = None
        if len(rest) >= 2 or dur - name_end >= 0.8:  # 「你好 Vesper，现在几点」：名字后面已经说了的话交给识别，不用再说一遍
            pre = round((eng.ring.total - (start_abs + int(name_end * SR))) / SR + 0.05, 2)
            pre = pre if 0 < pre <= 9 else None
        kw = ("你好" + P.NAME) if zh else "hey_" + P.NAME.lower()
        eng._judge({"kw": kw, "seg": vseg, "how": "asr", "t": t0, "langs": langs, "path": "asr",
                    "preroll_s": pre, "extra": {"text": text[:40], "seg_full_s": round(dur, 2), "path": "asr"}})


def _name_end_s(toks, ts, dur):
    """名字（…PER / …帕）最后一个 token 结束在第几秒；对不上就用整句长度。"""
    joined, owner = "", []
    for i, t in enumerate(toks):
        joined += t
        owner += [i] * len(t)
    hit = match_name(joined)
    if not hit or not ts or len(ts) != len(toks):
        return dur
    # match_name 去标点前后长度不变（标点换成空格），位置可以直接对回 token
    i = owner[min(hit[0], len(owner)) - 1]
    return ts[i + 1] if i + 1 < len(ts) else min(dur, ts[i] + 0.3)


def _selftest_clip():
    """自检用的录音：声纹录入时他自己说的「你好 Vesper」（w1…w5 里第一个存在的）。"""
    for k in ("w1", "w2", "w5", "w4", "w3"):
        f = Path(P.VOICEPRINT) / "enroll" / f"{k}.wav"
        if f.exists():
            try:
                return speaker.load_wav(f)
            except Exception:  # noqa: BLE001
                continue
    return None


def _hub_mod():
    try:
        from . import hub as h
        return h
    except Exception:  # noqa: BLE001
        return None


engine = None


def start(hub):
    """server.py 启动时调用。模型没下载就安静跳过（打印原因），不影响识别服务。"""
    global engine
    try:
        st = getattr(hub, "state", None)
        engine = WakeEngine(emit=hub.emit, get_state=st if callable(st) else (lambda: getattr(hub, "state", "idle")))
    except FileNotFoundError as e:
        print(f"[wake] 未启用：{e}")
        return None
    try:
        hub.subscribe_audio(engine.feed, name="wake")
    except TypeError:
        hub.subscribe_audio(engine.feed)
    try:
        hub.route("GET", "/wake/health", _http_health)
        if hasattr(hub, "add_status"):
            hub.add_status(lambda: {"wake_health": engine.health})
    except Exception as e:  # noqa: BLE001
        print(f"[wake] 自检接口没挂上：{e!r}", flush=True)
    print(f"[wake] 「{P.WAKE_WORD}」等 {len(P.WAKE_WORDS)} 种叫法唤醒已开启；声纹", "已录入" if speaker.profile().enrolled else "未录入",
          "；第二条路（SenseVoice 认名字）", "开" if engine.asr_path is not None and engine.asr_path.ready else "关", flush=True)
    return engine


_last_run = -1e9


def _http_health(method, path, query, body):
    """GET /wake/health：马上自检一次（?run=1）或返回最近一次结果。
    自检要跑两遍识别，别的网页也能发 GET 过来，所以 20 秒内最多真跑一次，其余返回上次结果。"""
    global _last_run
    now = time.monotonic()
    if query.get("run") and now - _last_run >= 20:
        _last_run = now
        h = engine.self_test()
    else:
        h = engine.health
    return 200, json.dumps(h, ensure_ascii=False).encode(), "application/json; charset=utf-8"
