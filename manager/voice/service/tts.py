"""Vesper 的中文语音合成（TTS）。

- 引擎可换：默认 sherpa-onnx + Kokoro v1.1 中文（82M，CPU 就够快）；
  可选 mlx-audio + Qwen3-TTS 0.6B CustomVoice（更自然，吃 GPU/内存多一些）。
  用哪个、哪个音色由用户试听后决定，存在 voice-tts.json 里。
- 边生成边说：DeepSeek 每吐一段字就喂进来，凑够一句（第一句在逗号处就先出声）
  立刻合成，交给 hub.play() 由 DeckVoice 播放（同一个音频引擎，回声消除才干净）。
- 「停」：hub 的 stop 事件一到，清掉待念队列、打断正在合成的那句。
- 边缘光：按播放时间线每秒约 15 次发 level 事件，rms = min(1, RMS × 6)。
- 省内存：第一次说话才加载模型，空闲 5 分钟自动卸载。

只依赖 numpy；引擎库按需 import。接口见 manager/voice/INTERFACE.md。
"""
from __future__ import annotations

import io
import json
import os
import queue
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

try:  # 作为 service 包的一部分运行
    from . import hub  # type: ignore
except Exception:  # 单独跑 tts_demo.py 时没有 hub
    hub = None  # type: ignore

SUPPORT = Path.home() / "Library/Application Support/Deck"
try:  # Windows 版（11-08）：路径跟 config.py 走（%APPDATA%\Deck\voice）；Mac 上不变
    from .config import ROOT as _ROOT, WIN as _WIN  # type: ignore
except Exception:  # 单独跑 tts_demo.py 时
    _WIN, _ROOT = False, None
_VOICE_DIR = Path(_ROOT) if _WIN else SUPPORT / "voice"
MODELS = Path(os.environ.get("DECK_VOICE_MODELS", _VOICE_DIR / "models"))
CONFIG = _VOICE_DIR / "tts.json"

IDLE_UNLOAD_S = int(os.environ.get("DECK_TTS_IDLE_S", 1800))  # 半小时没说话才卸载（冷启动要 5 秒，常用时别卸）
LEVEL_HZ = 15
PLAY_LEAD_S = 0.3  # 每次回答第一块延后这么多再播，给后面的块攒余量
LEVEL_GAIN = 6.0

# 音色表：id -> 引擎参数。试听后在 voice-tts.json 里选一个 id。
VOICES: dict[str, dict] = {
    # 按一句话描述造的声音：1.7B VoiceDesign 生成参考音频（只在挑声音时用），日常由 0.6B Base 克隆。
    # 参考音频在 voice/models/tts-refs/<ref>.wav，加新声音只要放一段 wav 再在这里加一行，不多占内存。
    # 选定：D1 为默认，D5 保留随时切换。
    "design:D1": {"engine": "qwen_clone", "ref": "D1", "label": "Vesper · 女声（清亮带笑）", "gender": "f"},
    "design:D5": {"engine": "qwen_clone", "ref": "D5", "label": "Vesper · 男声（干净温暖）", "gender": "m"},
    "design:D2": {"engine": "qwen_clone", "ref": "D2", "label": "温柔慵懒的女生", "gender": "f"},
    "design:D3": {"engine": "qwen_clone", "ref": "D3", "label": "元气少女", "gender": "f"},
    "design:D4": {"engine": "qwen_clone", "ref": "D4", "label": "知性助理女声", "gender": "f"},
    "design:D6": {"engine": "qwen_clone", "ref": "D6", "label": "低沉的 AI 管家男声", "gender": "m"},
    # 千问自带音色（CustomVoice 0.6B，约 1.9GB，tools/fetch_tts_models.sh 下载）。11-05 实测克隆音色约一半句子会
    # 跑成男声（tools/tts_drift.py，调温度/种子/长参考都没用），自带音色不跑调；但用户试听后还是选 D1，这里只作备选。
    # instruct 让它往 D1「清亮带笑」靠。
    "qwen:Vivian": {"engine": "qwen", "speaker": "Vivian", "label": "Vesper · 女声 Vivian（稳定）", "gender": "f",
                    "instruct": "用清亮、带笑意的年轻女声说话，语气轻快自然，像朋友聊天。"},
    "qwen:Serena": {"engine": "qwen", "speaker": "Serena", "label": "Vesper · 女声 Serena（温柔）", "gender": "f",
                    "instruct": "用温柔、亲切的年轻女声说话，语速自然。"},
    "kokoro:zf_001": {"engine": "kokoro", "sid": 3, "label": "Kokoro 女声 zf_001", "gender": "f"},
}
# 两个模型都念不准的词：送进 TTS 前替换成读音（可在 tts.json 的 "lexicon" 里追加）
# 试听时 Kokoro 女声把 Claude Code 念成 "clock co"，Qwen 念成 "cloud code"；其他英文词两者都念对了。
LEXICON = {"Claude Code": "克劳德 code", "Claude": "克劳德"}
DEFAULTS = {"voice": "design:D1", "speed": 1.0, "instruct": ""}  # 试听后定回 D1（跑调靠音高检测重合成兜底）
FALLBACK_VOICES = ["design:D1", "qwen:Vivian"]  # 选的音色没装时按顺序退
if _WIN:  # Windows 没有 mlx（Apple GPU），千问音色用不了，只用 Kokoro（windows/voice_models.py 下载）
    DEFAULTS["voice"] = "kokoro:zf_001"
    FALLBACK_VOICES = ["kokoro:zf_001"]


def _aname() -> str:
    """助手现在叫什么（Deck 设置里可以改）。"""
    try:
        from . import voicepaths as P
    except ImportError:
        try:
            import voicepaths as P
        except ImportError:
            return "Vesper"
    return P.NAME


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text("utf-8")))
    except Exception:
        pass
    if cfg.get("voice") not in VOICES or not _installed(VOICES[cfg["voice"]]):
        cfg["voice"] = next((v for v in FALLBACK_VOICES if _installed(VOICES[v])), FALLBACK_VOICES[0])
    return cfg


def save_config(**kw) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in kw.items() if v is not None})
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(CONFIG)
    return cfg


# ───────────────────────── 文字清洗 ─────────────────────────
_MD_CODE = re.compile(r"```.*?```", re.S)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MD_MARK = re.compile(r"(\*\*|__|`|^#{1,6}\s*|^\s*[-*+]\s+|^\s*>\s?)", re.M)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_DIGITS = "零一二三四五六七八九"


def _int_zh(n: int) -> str:
    if n == 0:
        return "零"

    def four(x: int) -> str:  # 0 < x < 10000
        out, zero = "", False
        for unit, base in (("千", 1000), ("百", 100), ("十", 10), ("", 1)):
            d = x // base % 10
            if d:
                out += ("零" if zero and out else "") + _DIGITS[d] + unit
                zero = False
            else:
                zero = True
        return out

    groups = []
    while n:
        groups.append(n % 10000)
        n //= 10000
    big = ["", "万", "亿", "万亿"]
    out = ""
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g:
            if out and g < 1000 and not out.endswith("零"):
                out += "零"
            out += four(g) + big[i]
        elif out and not out.endswith("零"):
            out += "零"
    out = re.sub(r"(^|[万亿零])二(?=[千万亿])", r"\1两", out.rstrip("零"))
    if out.startswith("一十"):
        out = out[1:]
    return out


def _num_zh(m: re.Match) -> str:
    s = m.group(0).replace(",", "")
    if "." in s:
        a, b = s.split(".", 1)
        return _int_zh(int(a or 0)) + "点" + "".join(_DIGITS[int(c)] for c in b)
    if len(s) > 1 and s.startswith("0") or len(s) > 12:  # 编号、电话：逐位念
        return "".join(_DIGITS[int(c)] for c in s)
    return _int_zh(int(s))


_UNITS = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*%"), lambda m: "百分之" + m.group(1)),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:GB|G)\b", re.I), lambda m: m.group(1) + "G"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*MB\b", re.I), lambda m: m.group(1) + "兆"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:°C|℃)"), lambda m: m.group(1) + "度"),
    (re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)"), lambda m: m.group(1) + "元"),
    (re.compile(r"\$\s*(\d+(?:\.\d+)?)"), lambda m: m.group(1) + "美元"),
    (re.compile(r"(\d{1,2}):(\d{2})"), lambda m: m.group(1) + "点" + (m.group(2) + "分" if m.group(2) != "00" else "")),
    (re.compile(r"(\d+)\s*[-~～]\s*(\d+)"), lambda m: m.group(1) + "到" + m.group(2)),
]
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def is_en(text: str) -> bool:
    """这段主要是英文吗（拉丁字母比汉字多三倍以上；夹几个英文词的中文算中文）。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return latin > 0 and latin > cjk * 3


def clean(text: str, numbers: bool = True) -> str:
    """把 DeepSeek 的 Markdown 回答变成适合念出来的纯文字。numbers=True 时把阿拉伯数字转成中文读法（英文段不转）。"""
    en = is_en(text)
    t = _MD_CODE.sub("" if en else "（代码就不念了）", text)
    if not en:  # 读音替换表是给中文句子用的（Claude→克劳德），英文句子里照原样念
        lex = {**LEXICON, **(load_config().get("lexicon") or {})}
        for k in sorted(lex, key=len, reverse=True):
            t = re.sub(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])", lex[k], t, flags=re.I)
    else:
        numbers = False
    t = _MD_LINK.sub(r"\1", t)
    t = _URL.sub("a link" if en else "链接", t)
    t = _MD_MARK.sub("", t)
    t = _EMOJI.sub("", t)
    t = t.replace("|", "，")
    if numbers:
        for pat, rep in _UNITS:
            t = pat.sub(rep, t)
        t = _NUM.sub(_num_zh, t)
    return re.sub(r"\s+", " ", t).strip()


# ───────────────────────── 按句切分 ─────────────────────────
_HARD = "。！？!?；;\n…"
_SOFT = "，,、：:"



def median_f0(pcm, sr, fmin=70.0, fmax=450.0):
    """粗略量一段音频的中位基频（Hz），用自相关，40ms 一帧；有声帧太少返回 None。只用来抓男女声跑偏。"""
    x = np.asarray(pcm, dtype=np.float32).reshape(-1)
    n = int(sr * 0.04)
    if len(x) < n * 3:
        return None
    lo, hi = int(sr / fmax), int(sr / fmin)
    rms_all = float(np.sqrt(np.mean(x * x)) + 1e-9)
    f0s = []
    for i in range(0, len(x) - n, n):
        fr = x[i:i + n] - np.mean(x[i:i + n])
        e = float(np.dot(fr, fr))
        if e / n < (0.5 * rms_all) ** 2 or e <= 0:
            continue  # 静音/气声帧
        ac = np.correlate(fr, fr, mode="full")[n - 1:]
        seg = ac[lo:min(hi, n - 1)]
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg)) + lo
        if ac[k] / e < 0.35:
            continue  # 不够周期，不算有声
        f0s.append(sr / k)
    return float(np.median(f0s)) if len(f0s) >= 3 else None

class Segmenter:
    """流式切句：按整句切（第一句很长时才在逗号处先放出去）；之后太短的句子和下一句合并，太长再按逗号切。
    每段单独合成会断语气，所以段尽量大；DeepSeek 出字比念得快，整句再念几乎不增加等待。"""

    def __init__(self, first_min=24, soft_max=60, next_min=14):
        self.buf = ""
        self.first = True
        self.first_min = first_min
        self.soft_max = soft_max
        self.next_min = next_min  # 第一句之后，太短的句子和下一句合在一起念，语气更连贯，也少一次合成启动

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out = []
        while True:
            cut = self._find_cut()
            if cut < 0:
                break
            seg, self.buf = self.buf[: cut + 1], self.buf[cut + 1:]
            if _speakable(seg):
                out.append(seg.strip())
                self.first = False
        return out

    def flush(self) -> list[str]:
        seg, self.buf = self.buf, ""
        self.first = True
        return [seg.strip()] if _speakable(seg) else []

    def _find_cut(self) -> int:
        b = self.buf
        # 英文一个字母念得比一个汉字快得多：长度门槛放大，句号「. 」也算一句结束
        k = 2.5 if is_en(b[:200]) else 1.0
        first_min, soft_max, next_min = self.first_min * k, self.soft_max * k, self.next_min * k
        for i, ch in enumerate(b):
            if ch == "." and i > 0 and b[i - 1].isalpha() and i + 1 < len(b) and b[i + 1] in " \n":
                if not self.first and i < next_min:
                    continue
                return i
            if ch in _HARD:
                if not self.first and i < next_min and ch != "\n":
                    continue
                # 别在小数点/省略号中间切：后面紧跟同类标点就继续吃
                j = i
                while j + 1 < len(b) and b[j + 1] in _HARD + "”\"'）)":
                    j += 1
                if j + 1 < len(b) or ch == "\n":
                    return j
                return -1 if ch in "…" else j
            if ch in _SOFT:
                if (self.first and i >= first_min) or i >= soft_max:
                    return i
        return -1


def _speakable(s: str) -> bool:
    return bool(re.search(r"[\w一-鿿]", s))


# ───────────────────────── 引擎 ─────────────────────────
class KokoroEngine:
    """sherpa-onnx Kokoro v1.1（中英混读，24kHz）。"""

    DIR = "kokoro-multi-lang-v1_1"
    numbers_in_text = True  # 数字交给 clean() 先转中文

    def __init__(self):
        if _WIN and not (MODELS / self.DIR / "voices.bin").exists():
            # 没下载时给一句看得懂的话（tts.error 事件里页面能直接显示），别让 onnxruntime 报一长串
            raise FileNotFoundError(f"语音合成模型还没下载（{MODELS / self.DIR}），在 Deck 设置 → 语音里点「下载语音模型」")
        import sherpa_onnx  # type: ignore

        d = MODELS / self.DIR
        if not (d / "model.onnx").exists() and (d / "model.int8.onnx").exists():
            model = d / "model.int8.onnx"
        else:
            model = d / "model.onnx"
        lex = ",".join(str(d / f) for f in ("lexicon-us-en.txt", "lexicon-zh.txt") if (d / f).exists())
        kw = dict(model=str(model), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
                  data_dir=str(d / "espeak-ng-data"))
        if (d / "dict").exists():
            kw["dict_dir"] = str(d / "dict")
        if lex:
            kw["lexicon"] = lex
        fsts = ",".join(str(d / f) for f in ("date-zh.fst", "phone-zh.fst", "number-zh.fst") if (d / f).exists())
        try:
            kcfg = sherpa_onnx.OfflineTtsKokoroModelConfig(**kw)
        except TypeError:  # 新版 sherpa-onnx 去掉了 dict_dir
            kw.pop("dict_dir", None)
            kcfg = sherpa_onnx.OfflineTtsKokoroModelConfig(**kw)
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(kokoro=kcfg, num_threads=4, provider="cpu"),
            rule_fsts=fsts, max_num_sentences=1)
        self.tts = sherpa_onnx.OfflineTts(cfg)
        self.sr = self.tts.sample_rate

    def stream(self, text, voice, speed, cancelled, instruct=""):
        chunks: list[np.ndarray] = []

        def cb(samples, progress):
            chunks.append(np.array(samples, dtype=np.float32))
            return 0 if cancelled() else 1

        audio = self.tts.generate(text, sid=voice["sid"], speed=speed, callback=cb)
        if chunks:
            yield from chunks
        elif len(audio.samples):
            yield np.asarray(audio.samples, dtype=np.float32)


class QwenEngine:
    """mlx-audio + Qwen3-TTS 0.6B CustomVoice（Apple GPU，原生流式）。"""

    REPO = os.environ.get("DECK_QWEN_TTS", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit")
    numbers_in_text = False  # 大模型自己会念数字（实测「百分之七十二」「61G」都对）

    def __init__(self):
        os.environ.setdefault("HF_HOME", str(MODELS / "hf"))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 模型已下载好，运行时不联网
        import mlx.core as mx  # type: ignore
        from mlx_audio.tts.utils import load_model  # type: ignore

        mx.set_cache_limit(256 * 1024 * 1024)  # 不设的话 Metal 缓存会把内存顶到 6GB+；设了稳定在约 3GB
        self.model = load_model(self.REPO)
        self.sr = getattr(self.model, "sample_rate", 24000)
        self._warm()  # 预热：首块从 0.35s 降到 0.17s

    def _warm(self):
        for _ in self.stream("预热。", {"speaker": "Vivian"}, 1.0, lambda: False):
            pass

    def stream(self, text, voice, speed, cancelled, instruct=""):
        kw = dict(text=text, voice=voice["speaker"], lang_code="english" if is_en(text) else "chinese",
                  stream=True, streaming_interval=getattr(self, "interval", 0.32))
        instruct = instruct or voice.get("instruct", "")
        if instruct:
            kw["instruct"] = instruct
        if speed and abs(speed - 1.0) > 1e-3:
            kw["speed"] = speed
        for r in self.model.generate(**kw):
            if cancelled():
                break
            self.sr = getattr(r, "sample_rate", self.sr)
            yield np.asarray(r.audio, dtype=np.float32).reshape(-1)


class QwenCloneEngine(QwenEngine):
    """Qwen3-TTS 0.6B Base：照着一段参考音频的声音说话。

    参考音频来自 1.7B VoiceDesign 按一句话描述造出的声音（只在挑声音时用一次），
    日常只加载这个 0.6B 小模型。mlx-audio 会按 (ref_text, 音频指纹) 缓存参考音频的编码，
    所以参考音频只在启动时读一次。
    """

    REPO = os.environ.get("DECK_QWEN_CLONE", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    REFS = MODELS / "tts-refs"
    REF_TEXT = "你好呀，我在。今天想让我帮你做点什么？"  # 参考音频的逐字文本

    def __init__(self):
        self.refs: dict[str, object] = {}
        super().__init__()

    def _ref(self, name):
        if name not in self.refs:
            from mlx_audio.utils import load_audio  # type: ignore

            self.refs[name] = load_audio(str(self.REFS / f"{name}.wav"), sample_rate=self.sr)
        return self.refs[name]

    def _warm(self):
        cur = VOICES.get(load_config()["voice"], {})
        if cur.get("engine") == "qwen_clone":
            for _ in self.stream("预热。", cur, 1.0, lambda: False):
                pass

    # 克隆音色在流式合成里偶尔会「跑调」（女声中途变男声）。三道保险：
    # 1) 采样收紧（默认 temperature .9 / top_p 1.0 太散）；2) 每段固定随机种子，同一句每次都一样；
    # 3) 每块出声前量一下音高，女声掉到 MIN_F0_F 以下（或男声高过 MAX_F0_M）就丢掉这段、换种子重合成，
    #    从已经播出去的位置接着放。
    SAMPLING = dict(temperature=0.6, top_k=30, top_p=0.85, repetition_penalty=1.05)
    SEED = 20261105
    # 11-06 日志：被判跑偏的块里 145～159Hz 的其实还是她（英文和句尾音高本来就低），真变男声的是 87～101Hz；
    # 门槛从 160 降到 135，少很多没必要的重合成（每次重合成都要从头再生成一遍，说话就会卡一下）
    MIN_F0_F, MAX_F0_M = 135.0, 230.0

    def _gen(self, kw, seed):
        import mlx.core as mx  # type: ignore

        mx.random.seed(seed)
        for r in self.model.generate(**kw):
            yield np.asarray(r.audio, dtype=np.float32).reshape(-1)

    def _off(self, f0, gender):
        if f0 is None:
            return False
        return f0 < self.MIN_F0_F if gender == "f" else (f0 > self.MAX_F0_M if gender == "m" else False)

    def stream(self, text, voice, speed, cancelled, instruct=""):
        kw = dict(text=text, ref_audio=self._ref(voice["ref"]), ref_text=voice.get("ref_text", self.REF_TEXT),
                  lang_code="english" if is_en(text) else "chinese", stream=True,
                  streaming_interval=getattr(self, "interval", 0.32), **self.SAMPLING)
        if speed and abs(speed - 1.0) > 1e-3:
            kw["speed"] = speed
        gender = voice.get("gender")
        played = 0  # 已经交出去播放的采样数
        for attempt in range(3):
            pos, drift = 0, False
            for pcm in self._gen(kw, self.SEED + attempt):
                if cancelled():
                    return
                if gender and attempt < 2 and self._off(median_f0(pcm, self.sr), gender):
                    drift = True
                    print(f"[tts] 音色跑偏（{gender} 声 f0={median_f0(pcm, self.sr):.0f}Hz），换种子重合成「{text[:12]}」",
                          flush=True)
                    break
                end = pos + len(pcm)
                if end > played:  # 重合成时跳过已经播过的部分
                    yield pcm[max(0, played - pos):]
                    played = end
                pos = end
            if not drift:
                return


ENGINES = {"kokoro": KokoroEngine, "qwen": QwenEngine, "qwen_clone": QwenCloneEngine}


# ───────────────────────── 播报流水线 ─────────────────────────
class Speaker:
    def __init__(self):
        self.lock = threading.RLock()
        self.engines: dict[str, object] = {}
        self.last_used = time.monotonic()
        self.q: queue.Queue = queue.Queue()
        self.epoch = 0
        self.seg = Segmenter()
        self.play_until = 0.0  # 估算的播放结束时刻（monotonic）
        self.levels: list[tuple[float, float]] = []  # (时刻, rms)
        self.busy = False
        self.speaking = False
        self.started = False
        self.pending: set[str] = set()  # 已交给 helper、还没收到 playback_done 的块
        self.n = 0
        self.t_first_text = None  # 这次回答第一段文字到达的时刻（量「文字 → 出声」延迟用）
        self.held_at = 0.0  # 打断暂停中（他开口了，还没判断是不是真要打断）：从这个时刻起播放冻结
        self.spoken: list[str] = []  # 最近已经开始播出来的两段文字（判断「是不是她自己的回声」用）

    # —— 生命周期 ——
    def start(self):
        if self.started:
            return
        self.started = True
        self.prewarm()  # 服务一启动就把合成模型加载好
        threading.Thread(target=self._worker, name="tts-worker", daemon=True).start()
        threading.Thread(target=self._ticker, name="tts-level", daemon=True).start()
        if hub is not None:
            hub.on("assistant.delta", lambda e: self.feed(e.get("text", ""), False))
            hub.on("assistant.done", lambda e: self.feed(e.get("text", ""), True))
            hub.on("stop", lambda e: self.cancel())
            hub.on("playback_done", lambda e: self.pending.discard(e.get("id")))
            # 预热：一被叫醒 / 开始听 / 开始说话就把合成模型准备好，免得空闲卸载后第一句要等几秒加载
            hub.on("wake", lambda e: self.prewarm())
            hub.on("listen", lambda e: e.get("on") and self.prewarm())
            hub.on("user.speaking", lambda e: self.prewarm())
            hub.on("user.final", lambda e: self.prewarm())

    def engine(self, name: str):
        with self.lock:
            eng = self.engines.get(name)
            if eng is None:
                eng = self.engines[name] = ENGINES[name]()
            self.last_used = time.monotonic()
            return eng

    def prewarm(self):
        self.last_used = time.monotonic()
        try:
            name = VOICES[load_config()["voice"]]["engine"]
        except Exception:  # noqa: BLE001
            return
        if name in self.engines or getattr(self, "_warming", False):
            return
        self._warming = True

        def go():
            t0 = time.monotonic()
            try:
                self.engine(name)
                print(f"[tts] 预热 {name} {int((time.monotonic() - t0) * 1000)}ms", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[tts] 预热失败 {type(e).__name__}: {e}", flush=True)
            finally:
                self._warming = False

        threading.Thread(target=go, name="tts-prewarm", daemon=True).start()

    def unload(self):
        with self.lock:
            self.engines.clear()
        import gc

        gc.collect()
        try:
            import mlx.core as mx  # type: ignore

            mx.clear_cache()
        except Exception:
            pass

    # —— 输入 ——
    def feed(self, delta: str, done: bool):
        """DeepSeek 的增量文字。done=True 表示这次回答结束（delta 可为空或最后一段）。"""
        if delta and self.t_first_text is None:
            self.t_first_text = time.monotonic()
        segs = self.seg.feed(delta or "")
        if done:
            segs += self.seg.flush()
        ep = self.epoch
        for s in segs:
            self.q.put((ep, s))

    def say(self, text: str):
        ep = self.epoch
        for s in _split_all(text):
            self.q.put((ep, s))

    def cancel(self):
        """停：作废队列和正在合成的句子（播放队列由 hub.stop() 清）。"""
        self.epoch += 1
        self.seg = Segmenter()
        self.t_first_text = None
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        with self.lock:
            self.levels.clear()
            self.play_until = 0.0
            self.pending.clear()
            self.held_at = 0.0
        _emit({"type": "level", "rms": 0.0, "src": "tts"})
        self.speaking = False

    def hold(self, on: bool):
        """打断第一步：原地暂停（helper 那边 player.pause）。合成照常往下做，块照常排队；
        放开时把估算的播放时间线整体往后挪暂停的时长，接着说。"""
        with self.lock:
            now = time.monotonic()
            if on and not self.held_at:
                self.held_at = now
            elif not on and self.held_at:
                d = now - self.held_at
                self.held_at = 0.0
                if self.play_until:
                    self.play_until += d
                self.levels = [(t + d, v) for t, v in self.levels]

    # —— 合成 ——
    def synth(self, text: str, voice_id: str | None = None, speed: float | None = None):
        """整段合成，返回 (sr, float32 数组)。给试听和外部调用用。"""
        cfg = load_config()
        v = VOICES.get(voice_id or cfg["voice"], VOICES[cfg["voice"]])
        eng = self.engine(v["engine"])
        txt = clean(text, numbers=eng.numbers_in_text)
        parts = list(eng.stream(txt, v, speed or cfg["speed"], lambda: False, cfg.get("instruct", "")))
        self.last_used = time.monotonic()
        return eng.sr, (np.concatenate(parts) if parts else np.zeros(0, np.float32))

    def _worker(self):
        while True:
            ep, text = self.q.get()
            if ep != self.epoch:
                continue
            cfg = load_config()
            v = VOICES[cfg["voice"]]
            self.busy = True
            try:
                eng = self.engine(v["engine"])
                txt = clean(text, numbers=eng.numbers_in_text)
                if not _speakable(txt):
                    continue
                t0, first, dur = time.monotonic(), None, 0.0
                seg_first = True
                # 一次回答的第一段用小块（0.32s）尽快出声；后面的段播放前已经有余量，用大块减少解码开销、合成更快
                eng.interval = 0.32 if (not self.speaking and self.play_until < time.monotonic()) else 0.64
                for pcm in eng.stream(txt, v, float(cfg["speed"]), lambda: ep != self.epoch, cfg.get("instruct", "")):
                    if ep != self.epoch:
                        break
                    if pcm.size:
                        if first is None:
                            first = time.monotonic() - t0
                            with self.lock:
                                self.spoken = (self.spoken + [txt])[-4:]
                        dur += pcm.size / eng.sr
                        st = self._play(pcm, eng.sr)
                        if seg_first:
                            seg_first = False
                            # 告诉页面这一句什么时候开始出声（资讯页早报逐句高亮、暂停后从这句继续）
                            _emit({"type": "tts.segment", "text": txt, "in_ms": int(max(0.0, st - time.monotonic()) * 1000)})
                total = time.monotonic() - t0
                print(f"[tts] 段 {len(txt)}字 首块 {int((first or 0) * 1000)}ms 合成 {int(total * 1000)}ms "
                      f"音频 {dur:.2f}s 实时率 {total / dur if dur else 0:.2f}：{txt[:40]}", flush=True)
            except Exception as e:  # 引擎坏了不能拖垮整个服务
                _emit({"type": "tts.error", "error": f"{type(e).__name__}: {e}"})
            finally:
                self.busy = False
                self.last_used = time.monotonic()

    def _play(self, pcm: np.ndarray, sr: int) -> float:
        """排进播放时间线，返回这一块开始出声的时刻（monotonic）。"""
        pcm = np.clip(pcm, -1.0, 1.0).astype(np.float32)
        now = time.monotonic()
        with self.lock:
            held = bool(self.held_at)
            if held:
                now = self.held_at  # 暂停中：按暂停那一刻排时间线，放开时整体后移
            if self.play_until < now + 0.05:
                if self.speaking and self.play_until > 0:
                    gap = now + 0.05 - self.play_until
                    if gap > 0.12:
                        print(f"[tts] 断档 {int(gap * 1000)}ms（合成跟不上播放）", flush=True)
                    start = now + 0.05
                else:
                    # 一次回答开头先垫一小段余量：合成偶尔慢一下（实时率接近 1）时不至于说一半卡住
                    start = now + PLAY_LEAD_S
            else:
                start = self.play_until
            hop = max(1, sr // LEVEL_HZ)
            n = len(pcm) // hop
            if n:
                frames = pcm[: n * hop].reshape(n, hop)
                rms = np.sqrt(np.mean(frames ** 2, axis=1))
                self.levels.extend((start + i / LEVEL_HZ, float(min(1.0, r * LEVEL_GAIN))) for i, r in enumerate(rms))
            self.play_until = start + len(pcm) / sr
        if not self.speaking:
            self.speaking = True
            if not held:  # 暂停中状态是「在听」，别改回「在说」；放开时由打断模块恢复
                _set_state("speaking")
            if self.t_first_text is not None:
                ms = int((time.monotonic() - self.t_first_text) * 1000)
                print(f"[tts] 第一段文字到出声 {ms}ms", flush=True)
                _emit({"type": "tts.first_audio", "ms": ms})
        if hub is not None:
            self.n += 1
            pid = f"tts-{self.n}"
            self.pending.add(pid)
            try:
                ok = hub.play(pcm, sr, id=pid)
            except TypeError:  # 老版本 hub 没有 id 参数
                ok = hub.play(pcm, sr) and False
            if not ok:  # helper 没连上或不回执：只按估算时长判断说完
                self.pending.discard(pid)
        return start

    # —— 边缘光 + 状态 + 空闲卸载 ——
    def _ticker(self):
        while True:
            time.sleep(1 / LEVEL_HZ)
            now = time.monotonic()
            if self.speaking:
                val = None
                with self.lock:
                    while self.levels and self.levels[0][0] <= now and not self.held_at:
                        val = self.levels.pop(0)[1]
                    # 估算播完 + helper 确认播完（helper 不回话时最多多等 2 秒）
                    ended = (not self.held_at and now > self.play_until + 0.15
                             and (not self.pending or now > self.play_until + 2.0))
                if val is not None and not self.held_at:
                    _emit({"type": "level", "rms": round(val, 3), "src": "tts"})
                if ended and self.q.empty() and not self.busy and not self.seg.buf:
                    self.speaking = False
                    self.t_first_text = None
                    _emit({"type": "level", "rms": 0.0, "src": "tts"})
                    # 说话途中他按键插话，状态已经是 listening 了，别把它改回 idle（会把他正在说的话截断）
                    if hub is None or hub.state() == "speaking":
                        _set_state("idle")
            elif self.engines and now - self.last_used > IDLE_UNLOAD_S and not self.busy:
                self.unload()


def _split_all(text: str) -> list[str]:
    s = Segmenter()
    return s.feed(text) + s.flush()


def _emit(ev: dict):
    if hub is not None:
        try:
            hub.emit(ev)
        except Exception:
            pass


def _set_state(st: str):
    if hub is not None:
        try:
            hub.set_state(st)
        except Exception:
            pass


def wav_bytes(pcm: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


# ───────────────────────── 对外接口 ─────────────────────────
_sp = Speaker()


def start():
    """服务启动时调一次：订阅 assistant.delta / assistant.done / stop。"""
    _sp.start()


def feed(text: str, done: bool = False):
    _sp.start()
    _sp.feed(text, done)


def say(text: str):
    _sp.start()
    _sp.say(text)


def hold(on: bool):
    _sp.hold(on)


def recent_spoken(n: int = 2) -> list[str]:
    with _sp.lock:
        return list(_sp.spoken[-n:])


def stop():
    _sp.cancel()
    if hub is not None:
        hub.stop("ui")


def synth(text: str, voice: str | None = None, speed: float | None = None):
    return _sp.synth(text, voice, speed)


def voices() -> list[dict]:
    cur = load_config()["voice"]
    out = []
    for k, v in VOICES.items():
        inst = _installed(v)
        out.append({"id": k, **{x: v[x] for x in ("engine", "label", "gender")}, "current": k == cur, "installed": inst})
    return out


def unload():
    _sp.unload()


def _installed(v: dict) -> bool:
    eng = v["engine"]
    if eng == "kokoro":
        return (MODELS / KokoroEngine.DIR / "voices.bin").exists()
    if _WIN:  # 千问音色要 mlx（只有 Mac 有）
        return False
    hub_dir = MODELS / "hf" / "hub"
    repo = QwenCloneEngine.REPO if eng == "qwen_clone" else QwenEngine.REPO
    if not (hub_dir / ("models--" + repo.replace("/", "--"))).exists():
        return False
    return eng != "qwen_clone" or (QwenCloneEngine.REFS / f"{v['ref']}.wav").exists()


def http(method: str, path: str, body: bytes | None = None):
    """service/server.py 路由未命中时调用；返回 (状态码, Content-Type, bytes) 或 None。"""
    path = path.split("?", 1)[0]
    try:
        data = json.loads(body or b"{}") if method == "POST" else {}
    except ValueError:
        return 400, "application/json", b'{"error":"bad json"}'
    if method == "GET" and path in ("/voices", "/tts/voices"):
        out = {"voices": voices(), "config": load_config()}
        return 200, "application/json", json.dumps(out, ensure_ascii=False).encode()
    if method == "POST" and path == "/tts/preview":
        vid = data.get("voice")
        if vid and vid not in VOICES:
            return 404, "application/json", b'{"error":"unknown voice"}'
        text = (data.get("text") or "你好，我是 %s。今天想让我帮你做点什么？" % _aname())[:200]
        sr, pcm = synth(text, vid, data.get("speed"))
        if data.get("play"):
            hub and hub.play(pcm, sr)
        return 200, "audio/wav", wav_bytes(pcm, sr)
    if method == "POST" and path == "/tts/config":
        if data.get("voice") and (data["voice"] not in VOICES or not _installed(VOICES[data["voice"]])):
            return 404, "application/json", b'{"error":"voice not installed"}'
        sp = data.get("speed")
        cfg = save_config(voice=data.get("voice"), speed=float(sp) if sp else None, instruct=data.get("instruct"))
        return 200, "application/json", json.dumps(cfg, ensure_ascii=False).encode()
    if method == "POST" and path == "/tts/say":
        say(str(data.get("text", ""))[:2000])
        return 200, "application/json", b'{"ok":true}'
    return None
