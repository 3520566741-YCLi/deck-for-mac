"""声纹：3D-Speaker CAM++（sherpa-onnx）判断「是不是用户在说话」。

打分 = 0.65 × 唤醒词文本相关分 + 0.35 × 普通话句子文本无关分
  - 文本相关：和录入的 5 遍「你好+名字」（+之后自动补录的）逐个算余弦，取最高 3 个的平均。
    同一句话比对，区分度最高。
  - 文本无关：和 3 句普通话的平均声纹比，防止只学到「这句话的发音」。
门槛：录入完用留一法算出自己的分数分布，门槛 = 自己平均分 − 0.10，夹在 [0.55, 0.75]，
宁可偶尔没叫醒，也不让别人叫醒（用户要求）。
自动补录：分数 ≥ 门槛 + 0.10 的唤醒，且和原始录入相比也足够像，才收进补录池（最多 30 条，
先进先出，每 10 分钟最多收 1 条），原始 5 条永远保留，防止越学越偏。
"""
import json
import os
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

try:
    from . import voicepaths as P
except ImportError:  # wake_cli.py 直接运行
    import voicepaths as P

SR = 16000
W_TD, W_TI = 0.65, 0.35
THR_FLOOR, THR_CEIL, THR_MARGIN = 0.55, 0.75, 0.10
ADAPT_MARGIN, ADAPT_MAX, ADAPT_EVERY_S = 0.10, 30, 600

_lock = threading.Lock()
_extractor = None


def _get_extractor():
    global _extractor
    if _extractor is None:
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(P.SPEAKER_MODEL), num_threads=1, provider="cpu")
        if not cfg.validate():
            raise RuntimeError(f"声纹模型无效：{P.SPEAKER_MODEL}")
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    return _extractor


def embed(samples):
    """16k float32 → 单位长度声纹向量。"""
    samples = np.asarray(samples, dtype=np.float32)
    ex = _get_extractor()
    with _lock:
        s = ex.create_stream()
        s.accept_waveform(sample_rate=SR, waveform=samples)
        s.input_finished()
        e = np.asarray(ex.compute(s), dtype=np.float32)
    n = np.linalg.norm(e)
    return e / n if n > 0 else e


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _private(root):
    """声纹目录只给自己读写（700/600）。"""
    try:
        os.chmod(root, 0o700)
        for f in Path(root).rglob("*"):
            os.chmod(f, 0o700 if f.is_dir() else 0o600)
    except OSError:
        pass


def save_wav(path, samples):
    pcm = np.clip(np.asarray(samples) * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_wav(path):
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


LANGS = ("zh", "en")
THR_FLOOR_LANG = {"zh": THR_FLOOR, "en": 0.50}  # 英文唤醒词只录 3 遍，留一法样本少，下限略低（合成声音冒充最高 0.23）


def lang_of(kw):
    """唤醒词标签是中文叫法还是英文叫法：带汉字（你好Vesper、嘿Vesper）算中文，纯英文（hey_vesper）算英文。"""
    return "zh" if kw and re.search(r"[\u4e00-\u9fff]", kw) else "en"


class Profile:
    """磁盘上的声纹档案：voiceprint/profile.npz + profile.json + enroll/*.wav

    v2（11-06）：中文「你好Vesper」和英文「Hey Vesper」各有一组唤醒词录音和各自的门槛；
    普通话句子的音色两种共用。旧档案（只有中文）照常能读，英文唤醒时退回用中文那组比。
    """

    def __init__(self, root=None):
        self.root = Path(root or P.VOICEPRINT)
        self.pools = {k: np.zeros((0, 0), np.float32) for k in LANGS}     # 原始唤醒词录入
        self.adapted = {k: np.zeros((0, 0), np.float32) for k in LANGS}   # 自动补录
        self.sent_centroid = None                    # 普通话句子平均声纹
        self.thresholds = {}
        self.meta = {}
        self._last_adapt = 0.0
        self.load()

    # 兼容旧调用（wake_cli、enroll 页面）
    @property
    def wake(self):
        return self.pools["zh"]

    @property
    def threshold(self):
        return self.thresholds.get("zh")

    def has(self, lang):
        return len(self.pools[lang]) >= 2 and lang in self.thresholds

    @property
    def enrolled(self):
        return self.threshold is not None and len(self.pools["zh"]) >= 3

    @property
    def version(self):
        return int(self.meta.get("v", 1))

    # ---------- 读写 ----------
    def load(self):
        f = self.root / "profile.npz"
        if not f.exists():
            return
        d = np.load(f)
        dim = d["wake"].shape[1]
        empty = np.zeros((0, dim), np.float32)

        def arr(k):
            return d[k] if k in d and d[k].size else empty
        self.pools = {"zh": d["wake"], "en": arr("wake_en")}
        self.adapted = {"zh": arr("adapted"), "en": arr("adapted_en")}
        self.sent_centroid = d["sent"] if "sent" in d and d["sent"].size else None
        try:
            self.meta = json.loads((self.root / "profile.json").read_text())
            self.thresholds = {"zh": float(self.meta["threshold"])}
            if self.meta.get("threshold_en") is not None:
                self.thresholds["en"] = float(self.meta["threshold_en"])
        except Exception:
            self.thresholds = {}

    def save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "profile.tmp.npz"
        np.savez(tmp, wake=self.pools["zh"], wake_en=self.pools["en"],
                 adapted=self.adapted["zh"], adapted_en=self.adapted["en"],
                 sent=self.sent_centroid if self.sent_centroid is not None else np.zeros(0, np.float32))
        tmp.replace(self.root / "profile.npz")
        self.meta["threshold"] = self.thresholds.get("zh")
        self.meta["threshold_en"] = self.thresholds.get("en")
        self.meta["adapted"] = int(len(self.adapted["zh"]))
        self.meta["adapted_en"] = int(len(self.adapted["en"]))
        self.meta["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (self.root / "profile.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2))
        _private(self.root)

    # ---------- 打分 ----------
    def _td(self, e, pool):
        if len(pool) == 0:
            return 0.0
        sims = pool @ e
        k = min(3, len(sims))
        return float(np.mean(np.sort(sims)[-k:]))

    def _lang(self, lang):
        """这门语言没录过就退回中文那组（旧档案）。"""
        return lang if self.has(lang) else "zh"

    def score(self, e, exclude=None, lang="zh"):
        lang = self._lang(lang)
        pool = self.pools[lang] if exclude is None else np.delete(self.pools[lang], exclude, axis=0)
        if len(self.adapted[lang]):
            pool = np.vstack([pool, self.adapted[lang]])
        td = self._td(e, pool)
        if self.sent_centroid is None:
            return td, td, None
        ti = float(self.sent_centroid @ e)
        return W_TD * td + W_TI * ti, td, ti

    def threshold_for(self, lang):
        return self.thresholds.get(self._lang(lang))

    # ---------- 录入 ----------
    def build(self, wake_embs, sent_embs, takes_meta=None, wake_en_embs=None, chain=None):
        """用录入结果建档，中英文各自按留一法定门槛。返回校准信息。"""
        dim = len(wake_embs[0])
        self.pools = {"zh": np.stack(wake_embs).astype(np.float32),
                      "en": np.stack(wake_en_embs).astype(np.float32) if wake_en_embs and len(wake_en_embs) >= 2
                      else np.zeros((0, dim), np.float32)}
        self.adapted = {k: np.zeros((0, dim), np.float32) for k in LANGS}
        self.sent_centroid = _unit(np.mean(np.stack(sent_embs), axis=0)).astype(np.float32) if sent_embs else None
        self.thresholds = {}
        cal = {}
        for lang in LANGS:
            pool = self.pools[lang]
            if len(pool) < 2:
                continue
            self.thresholds[lang] = 0.0           # 先占位，score() 才会用这门语言自己的那组
            loo = [self.score(pool[i], exclude=i, lang=lang)[0] for i in range(len(pool))]
            mean, low = float(np.mean(loo)), float(np.min(loo))
            # 某一遍自己都过不了，说明那遍太差（太远/太吵），门槛不为它降低，只提示
            thr = round(min(max(mean - THR_MARGIN, THR_FLOOR_LANG[lang]), THR_CEIL), 3)
            self.thresholds[lang] = thr
            cal[lang] = {"threshold": thr, "loo": [round(x, 3) for x in loo], "loo_mean": round(mean, 3),
                         "loo_min": round(low, 3), "weak": [i for i, x in enumerate(loo) if x < thr]}
        # 普通句子核对用的门槛：每句和其余句子的平均声纹比（留一法），再放宽 0.15
        sent_thr = 0.5
        if sent_embs and len(sent_embs) >= 2:
            S = np.stack(sent_embs)
            sl = [float(_unit(np.mean(np.delete(S, i, axis=0), axis=0)) @ S[i]) for i in range(len(S))]
            sent_thr = round(min(max(float(np.mean(sl)) - 0.15, 0.40), 0.65), 3)
        zh = cal["zh"]
        self.meta = {"v": 2, "chain": chain or "unknown", "wake_word": P.WAKE_WORD,
                     "created": time.strftime("%Y-%m-%d %H:%M:%S"), "model": P.SPEAKER_MODEL.name,
                     "sent_threshold": sent_thr,
                     "loo": zh["loo"], "loo_mean": zh["loo_mean"], "loo_min": zh["loo_min"],
                     "en": cal.get("en"), "takes": takes_meta or []}
        self.save()
        return {"threshold": zh["threshold"], "loo": zh["loo"], "loo_mean": zh["loo_mean"], "weak_takes": zh["weak"],
                "en": cal.get("en")}

    def maybe_adapt(self, e, score, lang="zh"):
        """高置信度唤醒才补录；还要和原始录入单独比也够像，防止漂移。中英文各补各的。"""
        if not self.enrolled or not self.has(lang):
            return False
        thr = self.thresholds[lang]
        if score < thr + ADAPT_MARGIN:
            return False
        if time.time() - self._last_adapt < ADAPT_EVERY_S:
            return False
        if self._td(e, self.pools[lang]) < thr + ADAPT_MARGIN / 2:
            return False
        ad = self.adapted[lang]
        if len(ad) and float(np.max(ad @ e)) > 0.97:
            return False  # 几乎重复，没信息量
        self.adapted[lang] = np.vstack([ad, e[None]])[-ADAPT_MAX:]
        self._last_adapt = time.time()
        self.save()
        return True


_profile = None


def profile():
    global _profile
    if _profile is None:
        _profile = Profile()
    return _profile


def reload():
    global _profile
    _profile = Profile()
    return _profile


def check(e, lang="zh"):
    """已有声纹向量 → {"ok","score","threshold",...}。lang：唤醒词是中文 zh 还是英文 en。"""
    p = profile()
    if not p.enrolled:
        return {"ok": False, "score": 0.0, "threshold": None, "reason": "not_enrolled"}
    s, td, ti = p.score(e, lang=lang)
    thr = p.threshold_for(lang)
    return {"ok": s >= thr, "score": round(s, 3), "td": round(td, 3),
            "ti": None if ti is None else round(ti, 3), "threshold": thr,
            "pool": p._lang(lang)}


def verify(samples_16k):
    """给识别线程用：一句话的音频 → 是不是用户。普通句子只用文本无关分（不是唤醒词）。"""
    samples_16k = np.asarray(samples_16k, np.float32)
    if len(samples_16k) < SR * 0.8:
        return {"ok": False, "score": 0.0, "reason": "too_short"}
    p = profile()
    if not p.enrolled or p.sent_centroid is None:
        return {"ok": False, "score": 0.0, "reason": "not_enrolled"}
    e = embed(samples_16k)
    ti = float(p.sent_centroid @ e)
    # 普通句子和录入句子是不同内容，分数整体偏低；用元数据里录入句子之间的互相似度做参照
    thr = float(p.meta.get("sent_threshold", 0.5))
    return {"ok": ti >= thr, "score": round(ti, 3), "threshold": thr}
