"""开口打断：先「暂停」，后「决定」。

第一步（stopword.py）：她在说/在想时听到人声，她先接着说；攒够约 1 秒人声核对声纹，是用户才原地暂停
  （helper 淡出 + pause，不丢任何东西），开始听他说。噪音、室友、电视不暂停（11-06 他嫌一点声音就打断太灵敏）。
第二步（准，asr.py 定稿后调 judge）：
  - 空的、只有「嗯啊呃 um hmm」→ 不算，接着说
  - 停止词 → 停（asr 里原有的 is_stop_phrase）
  - 回声：≥ 6 个字/词，且 70% 以上的相邻两字组合都在她刚播出来的两段里 → 是她自己的声音，接着说
  - 其余算真话：丢掉旧回答，回答新问题
放开时从原地接着说。暂停太久（> 8 秒）也自动放开，免得卡死。

只有回声消除开着时才做第一步（关着时麦克风里全是她自己的声音）。只记数字（日志里不存他说的话）。
"""
import json
import os
import re
import threading
import time

from . import hub


def _name():
    try:
        from . import voicepaths as P
        return P.NAME
    except Exception:  # noqa: BLE001
        return "Vesper"

FILLERS = {"嗯", "啊", "呃", "哦", "噢", "额", "唔", "哼", "嗯嗯", "啊啊", "um", "umm", "uh", "uhh", "hmm", "hm", "mm",
           "ah", "oh", "er", "erm", "uhhuh", "mhm", "mmhm", "mhmm", "ahha", "aha"}
# 单个字/词也要算打断的（停止类）：其余单字当附和
SINGLE_KEEP = {"停", "等", "别", "不", "stop", "wait", "no", "nope", "pause", "enough", "cancel", "hold", "停停",
               "等等", "不对", "不是", "算了", "闭嘴", "安静"}
MAX_HOLD_S = 8.0
LOG = os.path.join(os.path.expanduser("~"), "Library", "Logs", "Deck", "friday-barge.jsonl")

_lock = threading.Lock()
_held = {"on": False, "prev": "", "at": 0.0, "gen": 0, "judging": False}
HARD_MAX_S = 30.0  # 他说得再长，暂停也不超过这个


def log(rec):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(rec, ts=round(time.time(), 2)), ensure_ascii=False) + "\n")
    except OSError:
        pass


def held():
    return _held["on"]


def _tts():
    try:
        from . import tts
        return tts
    except Exception:  # noqa: BLE001
        return None


def hold(prev_state):
    """第一步：暂停。返回 False 表示已经在暂停中。"""
    with _lock:
        if _held["on"]:
            return False
        _held.update(on=True, prev=prev_state, at=time.monotonic(), gen=_held["gen"] + 1, judging=False)
        gen = _held["gen"]
    t = _tts()
    if t:
        t.hold(True)
    hub.send_helper({"t": "hold", "on": True})
    hub.emit({"type": "barge.hold", "prev": prev_state})

    def watchdog():
        if not (_held["on"] and _held["gen"] == gen):
            return
        if time.monotonic() - _held["at"] < HARD_MAX_S:
            if _held["judging"]:  # 他说完了，正在识别 / 判断：等结果（11-06 实测 7 秒长句被这里提前放开）
                threading.Timer(1.0, watchdog).start()
                return
            try:
                from . import asr
                if asr.L.active and asr.L.speech_started:  # 他还在说（长句）：再等等
                    threading.Timer(2.0, watchdog).start()
                    return
            except Exception:  # noqa: BLE001
                pass
        print("[barge] 暂停太久，接着说", flush=True)
        release("timeout")
    threading.Timer(MAX_HOLD_S, watchdog).start()
    return True


def judging():
    """asr 开始给暂停中听到的话定稿时调用：看门狗别在判断途中放开。"""
    if _held["on"]:
        _held["judging"] = True


def release(why):
    """不是真打断：从原地接着说。"""
    with _lock:
        if not _held["on"]:
            return
        prev, dur = _held["prev"], time.monotonic() - _held["at"]
        _held["on"] = False
    t = _tts()
    if t:
        t.hold(False)
    hub.send_helper({"t": "hold", "on": False})
    if t and getattr(t._sp, "speaking", False):
        prev = "speaking"  # 暂停时还在想，暂停期间第一段已经合成好排着了：放开就是在说
    st = hub.state()
    if st in ("listening", "idle", "thinking"):  # 定稿时 asr 会把状态设成「想」，放开时恢复
        hub.set_state(prev if prev in ("speaking", "thinking") else "idle")
    log({"event": "resume", "why": why, "held_s": round(dur, 2)})
    hub.emit({"type": "barge.resume", "why": why})


def commit(why):
    """真打断：清掉暂停（hub.stop 会清空播放并让页面取消旧回答）。"""
    with _lock:
        was, dur = _held["on"], time.monotonic() - _held["at"]
        _held["on"] = False
    if was:
        t = _tts()
        if t:
            t.hold(False)
        log({"event": "commit", "why": why, "held_s": round(dur, 2)})


def _on_stop(ev):
    # 任何「停」都会清空播放；helper 那边 stop 也会把音量恢复。这里只把暂停标记清掉
    with _lock:
        _held["on"] = False


def _bigrams(s):
    s = re.sub(r"[\s\W_]+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _units(s):
    """字数：汉字一个算一个，英文按词。"""
    return len(re.findall(r"[一-鿿]", s)) + len(re.findall(r"[A-Za-z']+", s))


def is_echo(text, spoken):
    if _units(text) < 6 or not spoken:
        return False  # 少于 6 个字永远不算回声（他常重复她的词来回答）
    ref = "".join(spoken[-2:])
    norm = lambda x: re.sub(r"[\s\W_]+", "", x.lower())  # noqa: E731
    if norm(text) and norm(text) in norm(ref):
        return True
    bg = _bigrams(text)
    if not bg:
        return False
    return len(bg & _bigrams(ref)) / len(bg) >= 0.7


def spoken(n=2):
    t = _tts()
    return t.recent_spoken(n) if t else []


# ---------- Jev 复核（：哪种更准用哪种） ----------
# 本机规则分得清「填充词 / 回声 / 停止词」，分不清「他在跟别人说话、在打电话、电视里的人在说话」。
# 规则判成真话时，再问一下 Jev（TypeSafe 快速选择模型，约 0.3 秒，只发文字）：它选「不是对 Vesper 说的」就接着说。
# 11-06 Mac 上 36 句对比：只用规则 33/36（跟别人说话、打电话那几句分不出来），Jev 35/36，漏掉真打断 0 次；
# Jev 的 confidence 对「ignore」那一类不准（选对了也常是 0.02～0.6），所以只看它选了哪一项，不卡把握值。
JEV_URL = "https://api.typesafe.ai/v1/systemone"
_jev_key = {"at": 0.0, "v": None}


def _key():
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"].strip()
    if time.time() - _jev_key["at"] < 300:
        return _jev_key["v"]
    v = None
    try:
        import subprocess
        v = subprocess.run(["security", "find-generic-password", "-s", "typesafe-api", "-w"],
                           capture_output=True, text=True, timeout=3).stdout.strip() or None
    except Exception:  # noqa: BLE001
        pass
    _jev_key.update(v=v, at=time.time() if v else 0.0)
    return v


def jev_says_not_for_her(text, spoken, timeout=1.2):
    """返回 (不是对她说的?, 把握, 毫秒)。Jev 没配置或出错时当作「是对她说的」。"""
    key = _key()
    if not key:
        return False, 0.0, 0
    import urllib.request
    q = {"barge": {"type": "choice", "criteria": {
        "interrupt": "The user is talking to their voice assistant: a new question or request, a correction, "
                     "or telling it to stop/wait",
        "ignore": "Not meant for the assistant: a filler or backchannel sound, the assistant's own speech picked up "
                  "again by the microphone (possibly with recognition errors), the user talking to another person or "
                  "on the phone, or TV/background speech"},
        "instructions": "While the voice assistant %s was speaking, the microphone picked up some speech and it "
                        "was transcribed. Decide whether %s should stop and answer it, or ignore it and keep "
                        "talking. The transcript can contain speech-recognition errors." % (_name(), _name())}}
    state = "%s had just said: %s\nThen the microphone heard: %s" % (_name(), " ".join(spoken[-2:])[:400], text[:300])
    body = json.dumps({"model": "jev-latest", "state": state, "questions": q}, ensure_ascii=False).encode()
    req = urllib.request.Request(JEV_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            a = (json.loads(r.read()).get("answers") or {}).get("barge") or {}
    except Exception:  # noqa: BLE001  Jev 只是复核，失败就按规则的结论
        return False, 0.0, int((time.perf_counter() - t0) * 1000)
    ms = int((time.perf_counter() - t0) * 1000)
    conf = float(a.get("confidence") or 0)
    return a.get("choice") == "ignore", conf, ms


def judge(text, use_jev=None):
    """第二步：返回 "real" | "filler" | "empty" | "echo" | "side"（Jev 判断不是对她说的）。停止词由 asr 先处理。"""
    t = (text or "").strip()
    n = re.sub(r"[\s\W_]+", "", t.lower())
    if not n:
        return "empty"
    if n in FILLERS or re.fullmatch(r"(嗯|啊|呃|哦|额|唔)+", n) or re.fullmatch(r"(u+m+|h+m+|u+h+|a+h+|o+h+)", n):
        return "filler"
    # 只有一个字/一个词、又不是停止类的词（「对」「好」「OK」「yeah」）：多半是附和或杂音，当杂音，接着说
    if _units(t) <= 1 and n not in SINGLE_KEEP:
        return "filler"
    t_mod = _tts()
    spoken = t_mod.recent_spoken(2) if t_mod else []
    if is_echo(t, spoken):
        return "echo"
    if use_jev is None:
        try:
            from .config import CFG
            use_jev = CFG.get("barge_jev", True)
        except Exception:  # noqa: BLE001
            use_jev = False
    if use_jev:
        side, conf, ms = jev_says_not_for_her(t, spoken)
        log({"event": "jev", "side": side, "conf": round(conf, 2), "ms": ms})
        if side:
            return "side"
    return "real"


def start():
    hub.on("stop", _on_stop)
