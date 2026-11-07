"""声纹录入：5 遍「你好+名字」（正常×2、远、小声、戴耳机）+ 3 种别的中文叫法
+ 3 遍「Hey+名字」+ 2 种别的英文叫法 + 3 句普通话。

不同叫法也录进同一门语言的那组：声纹里「最像的 3 遍」取平均，所以说「嘿+名字」时会去和录过的「嘿+名字」比，分数不会因为换了叫法而掉。

挂在语音服务 8778 上：GET /enroll 是页面，/enroll/api/* 是接口。录音也走 hub.subscribe_audio，
和平时唤醒同一条链路（同一个麦克风、同样的回声消除），所以录进去的声纹和平时听到的一致。
录入期间暂停唤醒，免得自己把自己叫醒。
"""
import json
import threading
import time
from pathlib import Path

import numpy as np

try:
    from . import speaker, voicepaths as P, wake
except ImportError:  # wake_cli.py 直接运行
    import speaker
    import voicepaths as P
    import wake

SR = 16000
HERE = Path(__file__).resolve().parent

N = P.NAME
STEPS = [
    {"id": "w1", "kind": "wake", "title": "正常说", "hint": "像平时叫人一样，自然地说"},
    {"id": "w2", "kind": "wake", "title": "再来一遍", "hint": "还是正常音量，语速可以随意一点"},
    {"id": "w3", "kind": "wake", "title": "站远一点", "hint": "离电脑两三米，或者转过身去说"},
    {"id": "w4", "kind": "wake", "title": "小声说", "hint": "像旁边有人在睡觉那样，轻声说"},
    {"id": "w5", "kind": "wake", "title": "戴耳机说", "hint": "戴上 AirPods 等耳机麦克风说；没有耳机可以跳过", "optional": True},
    {"id": "v1", "kind": "wake", "title": "换个叫法 · 嘿", "say": "嘿" + N, "hint": "平时随口叫的语气"},
    {"id": "v2", "kind": "wake", "title": "换个叫法 · 喂", "say": "喂" + N, "hint": "像找人那样叫一声"},
    {"id": "v3", "kind": "wake", "title": "换个叫法 · 早上好", "say": "早上好" + N, "hint": "也可以说晚上好" + N},
    {"id": "e1", "kind": "wake_en", "title": "英文 · 正常说", "say": "Hey " + N, "hint": "用平时叫它的英文说法", "optional": True},
    {"id": "e2", "kind": "wake_en", "title": "英文 · 再来一遍", "say": "Hey " + N, "hint": "语速随意，可以说 Hi " + N, "optional": True},
    {"id": "e3", "kind": "wake_en", "title": "英文 · 远一点或小声", "say": "Hey " + N, "hint": "离电脑远一点，或者轻声说", "optional": True},
    {"id": "f1", "kind": "wake_en", "title": "英文 · OK " + N, "say": "OK " + N, "hint": "随口叫一声", "optional": True},
    {"id": "f2", "kind": "wake_en", "title": "英文 · Good morning", "say": "Good morning " + N, "hint": "也可以说 Good evening " + N, "optional": True},
    {"id": "s1", "kind": "sent", "title": "第 1 句", "text": "今天天气不错，帮我看看下午有没有安排，再把桌面上的截图整理一下。"},
    {"id": "s2", "kind": "sent", "title": "第 2 句", "text": "周末晚上想听点轻松的音乐，顺便提醒我明天早上八点起床。"},
    {"id": "s3", "kind": "sent", "title": "第 3 句", "text": "把磁盘里没用的缓存清理掉，然后告诉我还剩多少空间。"},
]
LIMITS = {"wake": {"max": 5.0, "end_sil": 0.6, "min": 0.6},
          "wake_en": {"max": 5.0, "end_sil": 0.6, "min": 0.5},
          "sent": {"max": 16.0, "end_sil": 1.1, "min": 2.5}}


class Recorder:
    """收到音频 → 等开口 → 说完自动停（Silero VAD 判断，没有就用能量）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "idle"   # idle / waiting / recording / processing
        self.level = 0.0
        self.step = None
        self.buf = []
        self.pre = []
        self.vad = None
        self.done_cb = None
        self.t_arm = 0
        self.speech_n = 0
        self.sil_n = 0
        self.timeout_s = 10

    def _make_vad(self):
        try:
            import sherpa_onnx
            if not P.SILERO_VAD.exists():
                return None
            c = sherpa_onnx.VadModelConfig()
            c.silero_vad.model = str(P.SILERO_VAD)
            c.silero_vad.min_silence_duration = 0.25
            c.silero_vad.min_speech_duration = 0.15
            c.silero_vad.threshold = 0.4
            c.sample_rate = SR
            return sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)
        except Exception:
            return None

    def arm(self, step, done_cb):
        with self.lock:
            self.phase, self.step, self.done_cb = "waiting", step, done_cb
            self.buf, self.pre, self.speech_n, self.sil_n = [], [], 0, 0
            self.t_arm = time.time()
            self.vad = self._make_vad()
            self._win = []

    def cancel(self):
        with self.lock:
            self.phase, self.step = "idle", None

    def _is_speech(self, x):
        if self.vad is not None:
            self._win.append(x)
            data = np.concatenate(self._win)
            ws = 512
            k = len(data) // ws * ws
            for i in range(0, k, ws):
                self.vad.accept_waveform(data[i:i + ws])
            self._win = [data[k:]] if k < len(data) else []
            while not self.vad.empty():
                self.vad.pop()
            return self.vad.is_speech_detected()
        return float(np.sqrt(np.mean(x ** 2))) > 0.01

    def feed(self, x):
        x = np.asarray(x, np.float32)
        rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
        self.level = min(1.0, (rms * 12) ** 0.6)
        with self.lock:
            if self.phase not in ("waiting", "recording"):
                return
            kind = self.step["kind"]
            lim = LIMITS[kind]
            sp = self._is_speech(x)
            dur = len(x) / SR
            if self.phase == "waiting":
                self.pre = (self.pre + [x])[-6:]           # 开口前保留约 0.6 秒
                if sp:
                    self.phase, self.buf = "recording", list(self.pre)
                elif time.time() - self.t_arm > self.timeout_s:
                    self._finish(None, "没听到声音。确认麦克风开着，再点一次录音。")
                return
            self.buf.append(x)
            if sp:
                self.speech_n += dur
                self.sil_n = 0
            else:
                self.sil_n += dur
            total = sum(len(b) for b in self.buf) / SR
            if (self.sil_n >= lim["end_sil"] and self.speech_n > 0.3) or total >= lim["max"]:
                self._finish(np.concatenate(self.buf), None)

    def _finish(self, audio, err):
        cb, step = self.done_cb, self.step
        self.phase = "processing"
        threading.Thread(target=cb, args=(step, audio, err), daemon=True).start()


class Session:
    def __init__(self):
        self.takes = {}       # step id → {"audio","emb","info"}
        self.misses = {}      # step id → 唤醒模型没认出的次数
        self.msg = None
        self.result = None
        self.started = time.time()


rec = Recorder()
sess = None
# 录音来自哪条链路：正式服务里是 DeckVoice（和平时唤醒同一路声音）；wake_cli 调试时改成 raw-mic
CHAIN = "deckvoice"
_hub = None


def _wake_engine():
    return getattr(wake, "engine", None)


def _analyse(step, audio, err):
    global sess
    try:
        if err or audio is None:
            sess.msg = {"step": step["id"], "ok": False, "text": err or "没录到声音"}
            return
        kind = step["kind"]
        seg = wake.trim_speech(audio)
        dur = len(seg) / SR
        peak = float(np.max(np.abs(seg))) if len(seg) else 0
        rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0
        info = {"dur": round(dur, 2), "rms": round(rms, 4), "peak": round(peak, 3)}
        problems = []
        if dur < LIMITS[kind]["min"]:
            problems.append("太短了" if kind == "wake" else "这句需要完整读完")
        if peak >= 0.99:
            problems.append("声音太大爆音了，离麦克风远一点")
        if rms < (0.0012 if step["id"] == "w4" else 0.003):
            problems.append("声音太小，听不清")
        note = ""
        if kind in ("wake", "wake_en") and not problems:
            hit, span = wake.spot_offline(seg)
            info["kws_hit"] = hit
            # 11-06：「喂Kira」常被认成 hey_kira 之类（同一个名字、前缀相近），只要认出是在叫它就算，不再要求标签对上。
            # 唤醒模型没认出时：第一次请他再说一遍；同一步第二次还没认出就照样收下（声纹靠的是声音本身，
            # 不该因为小模型对某个叫法不敏感把人卡在这一步），并提示这种叫法平时可能偶尔叫不醒。
            if not hit:
                sess.misses[step["id"]] = sess.misses.get(step["id"], 0) + 1
                if sess.misses[step["id"]] < 2:
                    problems.append(f"没听出「{step.get('say') or P.WAKE_WORD}」，再说一遍试试（再不行也会收下）")
                else:
                    note = "（这种叫法唤醒模型不太认得，平时可能偶尔叫不醒）"
        if problems:
            sess.msg = {"step": step["id"], "ok": False, "text": "；".join(problems), "info": info}
            return
        e = speaker.embed(seg)
        # 和前面录过的唤醒词比一比，防止中途换了人或者噪音太大
        kind_of = {s["id"]: s["kind"] for s in STEPS}
        prev = [t["emb"] for k, t in sess.takes.items() if kind_of.get(k) == kind and k != step["id"]]
        if kind in ("wake", "wake_en") and len(prev) >= 2:
            c = float(speaker._unit(np.mean(prev, axis=0)) @ e)
            info["consistency"] = round(c, 3)
            # 换叫法的那几遍内容不同，和前面比天然低一点，放宽到 0.40
            if c < (0.40 if step.get("say") and step["id"][0] in "vf" else 0.45):
                sess.msg = {"step": step["id"], "ok": False, "info": info,
                            "text": "这遍和前几遍差别很大（可能有别人说话或噪音），再录一次"}
                return
        sess.takes[step["id"]] = {"audio": seg, "emb": e, "info": info}
        sess.msg = {"step": step["id"], "ok": True, "text": "好的，录上了" + note, "info": info}
    except Exception as ex:
        sess.msg = {"step": step["id"], "ok": False, "text": f"处理出错：{ex}"}
    finally:
        rec.phase = "idle"
        rec.step = None


def _finish():
    wake_ids = [s["id"] for s in STEPS if s["kind"] == "wake" and s["id"] in sess.takes]
    en_ids = [s["id"] for s in STEPS if s["kind"] == "wake_en" and s["id"] in sess.takes]
    sent_ids = [s["id"] for s in STEPS if s["kind"] == "sent" and s["id"] in sess.takes]
    if len(wake_ids) < 4 or len(sent_ids) < 3 or len(en_ids) == 1:
        return 400, {"error": f"还差几步没录完（「{P.WAKE_WORD}」至少 4 遍、句子 3 句；英文要么跳过，要么至少 2 遍）"}
    # 旧档案整个移到废纸篓，再写新的
    old = P.VOICEPRINT
    if old.exists():
        P.trash(old)
    d = P.VOICEPRINT / "enroll"
    d.mkdir(parents=True, exist_ok=True)
    for k in wake_ids + en_ids + sent_ids:
        speaker.save_wav(d / f"{k}.wav", sess.takes[k]["audio"])
    prof = speaker.Profile()
    cal = prof.build([sess.takes[k]["emb"] for k in wake_ids], [sess.takes[k]["emb"] for k in sent_ids],
                     takes_meta=[dict(sess.takes[k]["info"], id=k) for k in wake_ids + en_ids + sent_ids],
                     wake_en_embs=[sess.takes[k]["emb"] for k in en_ids], chain=CHAIN)
    speaker.reload()
    cal["weak_takes"] = [wake_ids[i] for i in cal["weak_takes"]]
    if cal.get("en"):
        cal["weak_takes"] += [en_ids[i] for i in cal["en"]["weak"]]
    sess.result = cal
    eng = _wake_engine()
    if eng:
        eng.resume("enroll")
    return 200, {"ok": True, **cal}


def state():
    eng = _wake_engine()
    prof = speaker.profile()
    steps = []
    for s in STEPS:
        t = sess.takes.get(s["id"]) if sess else None
        steps.append(dict(s, done=bool(t), info=t["info"] if t else None))
    return {
        "enrolled": prof.enrolled, "threshold": prof.threshold, "threshold_en": prof.thresholds.get("en"),
        "has_en": prof.has("en") if prof.enrolled else False, "version": prof.version if prof.enrolled else 0,
        "adapted": int(sum(len(a) for a in prof.adapted.values())) if prof.enrolled else 0,
        "updated": prof.meta.get("updated") if prof.enrolled else None,
        # 改名后：按旧名字录的声纹提示重录
        "wake_word": P.WAKE_WORD, "name": P.NAME,
        "wake_word_changed": bool(prof.enrolled and prof.meta.get("wake_word", P.WAKE_WORD) != P.WAKE_WORD),
        "session": bool(sess), "steps": steps,
        "rec": {"phase": rec.phase, "level": round(rec.level, 3), "step": rec.step["id"] if rec.step else None},
        "msg": sess.msg if sess else None, "result": sess.result if sess else None,
        "wake_on": bool(eng), "wake_paused": bool(eng and eng.paused_by),
        "last_wake": eng.last if eng else None,
        # 收音体检：最近有没有音频进来、音量多大（全 0 说明 DeckVoice 在送静音）
        "audio": eng.audio_info() if eng and hasattr(eng, "audio_info") else None,
        "models_ok": P.SPEAKER_MODEL.exists() and P.KWS_DIR.exists(),
    }


def _json(code, obj):
    return code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8"


def handle(method, path, query, body):
    global sess
    if method == "GET" and path.rstrip("/") == "/enroll":
        return 200, (HERE / "enroll.html").read_bytes(), "text/html; charset=utf-8"
    if not path.startswith("/enroll/api/"):
        return _json(404, {"error": "not found"})
    act = path[len("/enroll/api/"):]
    try:
        data = json.loads(body or b"{}") if method == "POST" else {}
    except Exception:
        data = {}
    if method == "GET" and act == "state":
        return _json(200, state())
    if method == "GET" and act == "take.wav":
        sid = (query.get("step") or [""])[0] if isinstance(query.get("step"), list) else query.get("step", "")
        t = sess.takes.get(sid) if sess else None
        if not t:
            return _json(404, {"error": "没有这遍录音"})
        import io
        import wave as _w
        b = io.BytesIO()
        with _w.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes(np.clip(t["audio"] * 32767, -32768, 32767).astype("<i2").tobytes())
        return 200, b.getvalue(), "audio/wav"
    if method != "POST":
        return _json(405, {"error": "method"})
    eng = _wake_engine()
    if act == "start":
        sess = Session()
        if eng:
            eng.pause("enroll")
        return _json(200, state())
    if act == "record":
        if not sess:
            return _json(400, {"error": "先开始录入"})
        step = next((s for s in STEPS if s["id"] == data.get("step")), None)
        if not step:
            return _json(400, {"error": "没有这一步"})
        sess.msg = None
        rec.arm(step, _analyse)
        return _json(200, state())
    if act == "cancel":
        rec.cancel()
        return _json(200, state())
    if act == "finish":
        code, obj = _finish()
        return _json(code, obj)
    if act == "abort":
        rec.cancel()
        sess = None
        if eng:
            eng.resume("enroll")
        return _json(200, state())
    return _json(404, {"error": "未知操作"})


def register(hub):
    global _hub
    _hub = hub
    try:
        hub.subscribe_audio(rec.feed, name="enroll")
    except TypeError:
        hub.subscribe_audio(rec.feed)
    hub.route("GET", "/enroll", handle)
    hub.route("POST", "/enroll", handle)
