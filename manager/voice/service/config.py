"""路径和可调参数。用户可在 ~/Library/Application Support/Deck/voice/config.json 覆盖任意键。"""
import json
import os
import sys

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))


def _deckos():
    """Windows 版（11-08）：Deck 跑在哪个系统上看 manager/deckos.py。服务可能在 manager/ 不在 sys.path 时启动
    （Mac 的 run.sh 是 cd manager/voice 再 -m service.server），这时按文件路径加载它；找不到就按 sys.platform 判断。"""
    if "deckos" in sys.modules:
        return sys.modules["deckos"]
    try:
        import deckos  # noqa: PLC0415
        return deckos
    except ImportError:
        pass
    p = os.path.join(os.path.dirname(os.path.dirname(HERE)), "deckos.py")
    try:
        import importlib.util  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location("deckos", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["deckos"] = mod
        return mod
    except Exception:  # noqa: BLE001
        return None


_OS = _deckos()
WIN = bool(_OS.WIN) if _OS is not None else sys.platform == "win32"
if WIN:
    # Windows：数据在 %APPDATA%\Deck\voice（和 Deck 其它数据放一起），日志在 %LOCALAPPDATA%\Deck\logs
    _DEFAULT_ROOT = _OS.support("Deck", "voice") if _OS is not None else os.path.join(
        os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming"), "Deck", "voice")
    LOGS = os.environ.get("DECK_VOICE_LOGS") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local"), "Deck", "logs")
else:
    _DEFAULT_ROOT = os.path.join(HOME, "Library", "Application Support", "Deck", "voice")
    LOGS = os.environ.get("DECK_VOICE_LOGS") or os.path.join(HOME, "Library", "Logs", "Deck")
ROOT = os.environ.get("DECK_VOICE_ROOT", _DEFAULT_ROOT)
MODELS = os.path.join(ROOT, "models")

DEFAULTS = {
    "http_port": 8778,          # 页面：SSE + HTTP
    "helper_port": 8779,        # DeckVoice.app 连这里
    "allowed_origins": ["http://127.0.0.1:8777", "http://localhost:8777"],
    # 识别
    "asr_engine": "qwen3",      # qwen3（Qwen3-ASR-0.6B，自带热词）| sensevoice（SenseVoice-Small，更快更省）
    "asr_threads": 4,
    "asr_idle_unload_s": 300,   # 空闲 5 分钟卸载
    "vad_threshold": 0.5,
    "end_silence_s": 0.6,       # 说完停顿多久算一句结束
    "max_utterance_s": 25,     # Qwen3-ASR 一次最多约 30 秒音频
    "listen_timeout_s": 6,      # 唤醒后多久没开口就回到待机
    "followup_s": 60,           # 助手说完后不用再喊唤醒词、直接接着说的窗口；0 关闭（选 1 分钟）
    "followup_voice_gate": True,  # 窗口里每句都核对声纹，不是他就忽略、窗口不关
    "followup_voice_floor": 0.35,  # 11-08：他接话「那你查呗」只有 0.43 被扔掉；她自己的声音 ≤0.16、合成冒充 ≤0.12
    "preroll_s": 0.4,           # 进入 listening 前多带的音频，防止吃掉第一个字
    "wake_preroll_s": 0.15,     # 唤醒词之后开始听：只多带一点
    "echo_tail_s": 0.7,         # 没回声消除：助手说完等多久再开追问窗口
    "echo_tail_aec_s": 0.4,
    "speaker_gate": False,      # True：每句都核对声纹，不是用户就丢弃
    # 「停」
    "stop_phrases": ["停", "停下", "停一下", "停停", "别说了", "闭嘴", "安静", "算了", "stop", "shut up", "be quiet",
                     "stop it", "stop talking"],
    "stop_kws_threshold": 0.3,
    # 开口打断（要回声消除开着，否则麦克风里全是她自己的声音）。实测：一点环境声就打断，太灵敏。
    # 现在像人一样：听到人声时她先接着说，攒够 barge_confirm_s 秒人声核对声纹，是用户才原地暂停、开始听；
    # 噪音、室友、电视都不暂停。暂停后识别出来的话再由规则 + Jev 判断是不是对她说的（见 barge.py）
    "barge_voice": True,
    "barge_confirm_s": 0.8,     # 攒够这么多人声再核对声纹（声纹最少要 0.8 秒；11-06 从 1.0 调快）
    "barge_lead_s": 0.35,       # 开始攒时把 VAD 报「在说话」之前的这一小段也带上
    "barge_duck": True,         # 听到人声先把她的音量压到 40%（不停），声纹确认是他才暂停，不是他就恢复
    "barge_recheck_s": 2.0,     # 第一次不像他、人还在说：攒到这么长再核对一次（长一点更准）
    "barge_score_margin": 0.15, # 打断片段短、带回声消除，分数比录入时低：门槛 = 档案门槛 − 这个（11-06 重录后档案 0.65 → 0.50）
    "barge_score_floor": 0.45,  # 门槛再低也不低于这个（11-06 校准：别人最高 0.31，他最低 0.54）
    "barge_min_score": 0.35,    # 回声消除关着时，说话中听到停止词要声纹 ≥ 这个才算他说的
    "barge_jev": True,          # 规则判成真话后让 Jev 复核「是不是对她说的」（跟别人说话、电视）；没配 Jev key 就跳过
    "barge_preroll_s": 1.0,     # 暂停时把他已经说出口的这段也带上，别吃字（核对声纹那段会自动加上）
    "aec_auto": True,           # 只在她想/说的时候开回声消除，平时关着（不影响别的 App 用麦克风、唤醒声纹分）
    "aec_off_delay_s": 2.0,     # 回到待机多久后关回声消除（连续对话时别来回切）
}


def load():
    cfg = dict(DEFAULTS)
    p = os.path.join(ROOT, "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[config] 读取 {p} 失败：{e}", flush=True)
    return cfg


CFG = load()


def model_path(*parts):
    return os.path.join(MODELS, *parts)


def find_model_dir(prefix):
    """在 models/ 下找名字以 prefix 开头的最新目录（按名字排序，日期在名字里）。"""
    try:
        names = sorted(n for n in os.listdir(MODELS) if n.startswith(prefix) and os.path.isdir(os.path.join(MODELS, n)))
    except FileNotFoundError:
        return None
    return os.path.join(MODELS, names[-1]) if names else None
