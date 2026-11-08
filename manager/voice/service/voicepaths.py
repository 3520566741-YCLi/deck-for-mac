"""唤醒/声纹用到的路径。全部在内置盘的 ~/Library/Application Support/Deck/voice 下。

可用环境变量覆盖（测试用）：DECK_VOICE_HOME、DECK_VOICE_MODELS。
"""
import os
import shutil
from pathlib import Path

HOME = Path(os.environ.get(
    "DECK_VOICE_HOME", os.environ.get("DECK_VOICE_ROOT") or
    Path.home() / "Library" / "Application Support" / "Deck" / "voice"))
MODELS = Path(os.environ.get("DECK_VOICE_MODELS", HOME / "models"))

KWS_DIR = MODELS / "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
SPEAKER_MODEL = MODELS / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
SILERO_VAD = MODELS / "silero_vad" / "silero_vad.onnx"  # 和识别线程共用一份

# 声纹档案：embeddings、录入原声（16k wav，方便以后换模型重算）、调参日志
VOICEPRINT = HOME / "voiceprint"
WAKE_LOG = Path.home() / "Library" / "Logs" / "Deck" / "friday-wake.jsonl"  # 文件名沿用，别的工具在读

# 助手的名字（：只用英文名；Kira → Vesper）。11-08 起在 Deck 设置 →「助手」里直接改（写 HOME/wake_name.txt，
# 唤醒模型当场重建，不用重启），改完按新名字重录声纹。读法见 names.py（词典 + 规则 + 用户填的读法提示）。
ZH_PREFIXES = ["你好", "嘿", "嗨", "哈喽", "你好呀", "喂", "在吗", "醒醒", "早上好", "早安", "下午好", "晚上好"]
EN_PREFIXES = ["hey", "hi", "hello", "okay", "ok", "yo", "hey there", "good morning", "good afternoon",
               "good evening", "wake up"]
# 不收单独一个名字（两个音节，聊天里太容易撞上），也不收名字放开头的叫法（Kira在吗 / kira wake up）：
# 11-06 合成声音扫参时这类在任何门槛下都一次没中，KWS 小模型要前面有个词垫着才认得出名字。


def phrases(name):
    """名字 → 中文叫法、英文叫法。中文名字（小美）只配中文前缀：英文前缀 + 汉字在唤醒模型里读不准。"""
    if any("\u4e00" <= c <= "\u9fff" for c in name):
        return [p + name for p in ZH_PREFIXES], []
    return [p + name for p in ZH_PREFIXES], [f"{p} {name.lower()}" for p in EN_PREFIXES]


DEFAULT_NAME = "Vesper"  # 选定（合成声音测 41 个候选，少见名字里它最好叫醒：约 72%，零误唤醒）


def _read_name():
    """wake_name.txt：第一行名字，第二行（可选）读法提示，例如「维斯帕」或「vest per」。"""
    try:
        lines = (HOME / "wake_name.txt").read_text(encoding="utf-8").splitlines()
        n = lines[0].strip() if lines else ""
        if n:
            return n, (lines[1].strip() if len(lines) > 1 else "")
    except Exception:
        pass
    return DEFAULT_NAME, ""


def _apply(name, hint):
    global NAME, NAME_HINT, WAKE_WORD, WAKE_WORDS_ZH, WAKE_WORDS_EN, WAKE_WORDS
    NAME, NAME_HINT = name, hint
    WAKE_WORD = "你好" + NAME
    WAKE_WORDS_ZH, WAKE_WORDS_EN = phrases(NAME)
    WAKE_WORDS = WAKE_WORDS_ZH + WAKE_WORDS_EN


_apply(*_read_name())


def save_name(name, hint=""):
    """写 wake_name.txt 并更新本模块里的名字（调用方负责重建唤醒模型，见 wake.rename）。
    别的进程（Deck 管家 8777）每次用到名字时自己读这个文件。"""
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = HOME / "wake_name.txt.tmp"
    tmp.write_text(name + ("\n" + hint if hint else "") + "\n", encoding="utf-8")
    tmp.replace(HOME / "wake_name.txt")
    _apply(name, hint)


def kws_file(*patterns):
    """在 KWS 目录里按顺序找第一个匹配的文件（下载脚本只留下需要的几个）。"""
    for pat in patterns:
        hits = sorted(KWS_DIR.glob(pat))
        if hits:
            return str(hits[0])
    raise FileNotFoundError(f"{KWS_DIR} 里找不到 {patterns}，先运行 models/fetch_wake_models.sh")


def trash(path):
    """移到废纸篓，绝不直接删除（管家铁律）。"""
    path = Path(path)
    if not path.exists():
        return None
    bin_ = Path.home() / ".Trash"
    bin_.mkdir(exist_ok=True)
    dest = bin_ / path.name
    i = 1
    while dest.exists():
        dest = bin_ / f"{path.stem} {i}{path.suffix}"
        i += 1
    shutil.move(str(path), str(dest))
    return dest
