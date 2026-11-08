"""Deck 设置 →「助手」用的接口：改名字、名字体检（合成声音试叫）、唤醒/声纹测试。挂在语音服务 8778 的 /assist/ 下。

- GET  /assist/state            名字、声纹状态、正在跑的体检/测试
- POST /assist/name             {name, hint}  改名，当场生效（唤醒模型重建，不用重启）
- POST /assist/namecheck        {name, hint}  名字体检：用 macOS 自带的合成声音把各种叫法说一遍，算两条唤醒路能叫醒几成、
                                会不会被普通聊天误叫醒（和 wake_cli.py names 同一个方法，只是放进后台、能看进度）
- GET  /assist/namecheck        体检进度和结果
- POST /assist/test/start       {mode: me|other|quiet, rounds, seconds}
                                me    = 你自己说 N 遍，算唤醒率和声纹通过率
                                other = 请别人说 N 遍，看声纹能不能拦住（被放过几次）
                                quiet = 平时聊天/放视频几分钟，算误唤醒（名字被听到几次、声纹放过几次）
- POST /assist/test/stop
- GET  /assist/test             测试进度和结果

测试期间唤醒照常在听，但结果只交给测试、不真的唤醒，也绝不拿测试时的声音补录声纹（可能是别人）。
测试最长 12 分钟，到点自动结束；录入声纹期间不能测。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    from . import enroll, names, speaker, voicepaths as P, wake
except ImportError:  # 直接运行
    import enroll
    import names
    import speaker
    import voicepaths as P
    import wake

MAX_TEST_S = 12 * 60


def _json(code, obj):
    return code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8"


def _engine():
    return getattr(wake, "engine", None)


# ---------------------------------------------------------------- 状态
def state():
    prof = speaker.profile()
    eng = _engine()
    meta = prof.meta if prof.enrolled else {}
    try:
        n, _ = wake.check_name(P.NAME, getattr(P, "NAME_HINT", ""))
    except Exception:  # noqa: BLE001  模型没下好
        n = 0
    return {
        "name": P.NAME, "hint": getattr(P, "NAME_HINT", ""), "default": P.DEFAULT_NAME,
        "wake_word": P.WAKE_WORD, "phrases": len(P.WAKE_WORDS), "prons": n,
        "cjk": bool(names.CJK.search(P.NAME)),
        "aliases": len(names.load_aliases(P.NAME)),
        "enrolled": prof.enrolled,
        "wake_word_changed": bool(prof.enrolled and meta.get("wake_word", "你好星期五") != P.WAKE_WORD),
        "enrolled_name": (meta.get("wake_word") or "")[2:] if prof.enrolled else "",
        "has_en": prof.has("en") if prof.enrolled else False,
        "threshold": prof.threshold if prof.enrolled else None,
        "threshold_en": prof.thresholds.get("en") if prof.enrolled else None,
        "updated": meta.get("updated"),
        "wake_on": eng is not None,
        "asr_path": bool(eng and eng.asr_path is not None and eng.asr_path.ready),
        "health": eng.health if eng else None,
        "enrolling": bool(enroll.sess),
        "models_ok": P.SPEAKER_MODEL.exists() and P.KWS_DIR.exists(),
        "can_synth": sys.platform == "darwin",
        "namecheck": _check.public() if _check else None,
        "test": _test.public() if _test else None,
    }


# ---------------------------------------------------------------- 改名
_rename_lock = threading.Lock()


def do_rename(data):
    name, hint, err = names.validate(data.get("name"), data.get("hint"))
    if err:
        return 400, {"error": err}
    if enroll.sess:
        return 409, {"error": "正在录声纹，录完或退出录入再改名字"}
    if _test and _test.running:
        return 409, {"error": "正在测试，先停下测试再改名字"}
    with _rename_lock:
        try:
            n, _ = wake.check_name(name, hint)
        except FileNotFoundError:
            return 503, {"error": "唤醒模型还没下载好（运行 models/fetch_wake_models.sh）"}
        if n == 0:
            return 400, {"error": "唤醒模型念不出这个名字。在「读法」里写个中文谐音试试（比如 Kaia 写「凯娅」）"}
        try:
            wake.rename(name, hint)
        except Exception as e:  # noqa: BLE001
            return 500, {"error": f"换名字没成功，已经退回原来的名字：{e}"}
        # 体检时合成声音被识别成的写法，名字对得上就收下当别名（以后真人说也常这么写）
        if _check and _check.name == name and _check.result:
            heard = _check.result.get("aliases") or []
            if heard:
                names.save_aliases(name, heard)
                wake._reset_matcher()
    return 200, state()


# ---------------------------------------------------------------- 名字体检（合成声音）
NEG_ZH = ["今天天气怎么样", "帮我打开音乐", "我们晚上吃什么", "你好呀，好久不见", "早上好，今天天气不错", "在吗，帮我看一下",
          "醒醒，该上班了", "把灯关掉吧", "明天早上八点开会", "这个周末去哪玩", "我刚刚在看电影", "喂，你听得到吗",
          "下午好，各位同学", "晚上好，欢迎收看新闻"]
NEG_EN = ["Hey Siri what's the weather", "Okay Google", "Good morning everyone", "Hello Kitty is cute",
          "Wake up, it's late", "Are you there", "Hey, how are you doing", "Hi there, nice to meet you",
          "Okay, sounds good", "Good evening and welcome", "Hey guys, check this out", "Hello, can you hear me",
          "Yo, what's up", "Good afternoon, team"]
ZH_VOICES = ["Tingting", "Lili", "Meijia", "Yu-shu", "Sinji"]
EN_VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Alex", "Fred"]


def _voices():
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout.splitlines()
    have = {}
    for ln in out:
        nm = re.split(r"\s{2,}|\s\(", ln)[0].strip()
        loc = re.search(r"\b([a-z]{2}_[A-Z]{2})\b", ln)
        if nm and loc:
            have[nm] = loc.group(1)
    zh = [v for v in ZH_VOICES if v in have] or [v for v, l in have.items() if l.startswith("zh_")]
    en = [v for v in EN_VOICES if v in have] or [v for v, l in have.items() if l in ("en_US", "en_GB", "en_AU")]
    return zh[:2], en[:3]


def _synth(text, voice, out):
    a = out + ".aiff"
    if subprocess.run(["say", "-v", voice, "-r", "190", "-o", a, text], capture_output=True, timeout=30).returncode:
        return None
    try:
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", a, out], check=True,
                       capture_output=True, timeout=30)
        return speaker.load_wav(out)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            os.remove(a)   # 自己刚生成的中间文件（不是用户文件）；最后整个临时目录会移进废纸篓
        except OSError:
            pass


class NameCheck:
    def __init__(self, name, hint):
        self.name, self.hint = name, hint
        self.running, self.done, self.total = True, 0, 0
        self.result, self.error = None, None
        self.started = time.time()
        threading.Thread(target=self._run, name="namecheck", daemon=True).start()

    def public(self):
        return {"name": self.name, "hint": self.hint, "running": self.running, "done": self.done, "total": self.total,
                "result": self.result, "error": self.error}

    def _run(self):
        tmp = None
        try:
            if sys.platform != "darwin":
                raise RuntimeError("名字体检要用 macOS 自带的合成声音，这台机器上没有")
            zh_v, en_v = _voices()
            if not zh_v and not en_v:
                raise RuntimeError("系统里没有可用的中文或英文合成声音")
            zh, en = P.phrases(self.name)
            pos = [(w, v) for w in zh[:8] for v in zh_v] + [(w, v) for w in en[:6] for v in en_v]
            neg = [(w, v) for w in NEG_ZH for v in zh_v[:1]] + [(w, v) for w in NEG_EN for v in en_v[:1]]
            self.total = len(pos) + len(neg) + 1
            s = wake.settings()
            body, n = wake.build_keywords(zh + en, "你好" + self.name, s["kws_threshold"], s["kws_score"],
                                          s["kws_threshold_en"], s["kws_score_en"], name=self.name, hint=self.hint)
            if n == 0:
                raise RuntimeError("唤醒模型念不出这个名字，在「读法」里写个中文谐音试试")
            tmp = tempfile.mkdtemp(prefix="deck-namecheck-")
            kwf = os.path.join(tmp, "keywords.txt")
            Path(kwf).write_text(body, encoding="utf-8")
            kws = wake.make_spotter(kwf)          # 单独一份，不碰正在用的唤醒模型
            self.done += 1
            eng = _engine()
            asr = eng.asr_path if eng and eng.asr_path is not None and eng.asr_path.ready else None
            m = names.Matcher(self.name, self.hint, (), min_sim=s.get("name_match_min", 0.8))

            def spot(x):
                import numpy as np
                st = kws.create_stream()
                st.accept_waveform(16000, np.concatenate([x, np.zeros(9600, np.float32)]))
                st.input_finished()
                while kws.is_ready(st):
                    kws.decode_stream(st)
                    if kws.get_result(st):
                        return True
                return False

            rows, heard = [], {}
            for i, (w, v) in enumerate(pos):
                x = _synth(w, v, os.path.join(tmp, f"p{i}.wav"))
                self.done += 1
                if x is None:
                    continue
                k = spot(x)
                a = None
                if asr is not None:
                    text = asr.transcribe(x)[0]
                    a = m.find(text.upper()) is not None
                    t = re.sub(r"[，。！？、,.!?\s]", "", text.upper())
                    for p in sorted([q.upper().replace(" ", "") for q in P.ZH_PREFIXES + P.EN_PREFIXES], key=len, reverse=True):
                        if t.startswith(p):
                            t = t[len(p):]
                            break
                    if 1 <= len(t) <= 8:
                        heard[t] = heard.get(t, 0) + 1
                rows.append({"w": w, "kws": k, "asr": a})
            fa_k, fa_a, fa_heard, neg_text = [], [], [], []
            for i, (w, v) in enumerate(neg):
                x = _synth(w, v, os.path.join(tmp, f"n{i}.wav"))
                self.done += 1
                if x is None:
                    continue
                if spot(x):
                    fa_k.append(w)
                if asr is not None:
                    nt = (asr.transcribe(x)[0] or "").upper()
                    neg_text.append(nt)
                    if m.find(nt):
                        fa_a.append(w)
                        fa_heard.append(nt[:40])   # 认成了什么，方便查为什么误叫醒
            if not rows:
                raise RuntimeError("合成声音没生成出来")
            nk = sum(r["kws"] for r in rows)
            na = sum(1 for r in rows if r["asr"])
            either = sum(1 for r in rows if r["kws"] or r["asr"])
            lex = names.lexicon()
            aliases = [t for t, c in sorted(heard.items(), key=lambda kv: -kv[1])
                       if not (t.isascii() and t in lex and t != self.name.upper())]
            aliases = names.clean_aliases(self.name, self.hint, aliases)
            # 收下别名后会不会让反例误叫醒：会的那个别名不要
            aliases = [a for a in aliases
                       if not any(names.Matcher(self.name, self.hint, (a,), min_sim=9).find(t) for t in neg_text)][:8]
            missed = sorted({r["w"] for r in rows if not (r["kws"] or r["asr"])})
            rate = either / len(rows)
            self.result = {
                "n": len(rows), "kws": round(nk / len(rows), 3), "asr": round(na / len(rows), 3) if asr else None,
                "either": round(rate, 3), "false_kws": fa_k, "false_asr": fa_a, "false_heard": fa_heard, "neg_n": len(neg),
                "missed": missed[:8], "aliases": aliases, "heard": [t for t, _ in sorted(heard.items(), key=lambda kv: -kv[1])][:6],
                "voices": zh_v + en_v,
                "verdict": "good" if rate >= 0.7 and not (fa_k or fa_a) else "ok" if rate >= 0.45 and len(fa_k) + len(fa_a) <= 1 else "poor",
            }
        except Exception as e:  # noqa: BLE001
            self.error = str(e) or repr(e)
        finally:
            self.running = False
            if tmp:
                try:
                    P.trash(tmp)
                except Exception:  # noqa: BLE001
                    pass


_check = None


def do_namecheck(data):
    global _check
    name, hint, err = names.validate(data.get("name"), data.get("hint"))
    if err:
        return 400, {"error": err}
    if _check and _check.running:
        if time.time() - _check.started < 300:
            return 409, {"error": "上一次体检还没跑完"}
    _check = NameCheck(name, hint)
    return 200, _check.public()


# ---------------------------------------------------------------- 唤醒 / 声纹测试
class Test:
    READY_S, SPEAK_S, GRACE_S, GAP_S = 1.4, 4.5, 1.5, 1.0

    def __init__(self, mode, rounds, seconds):
        self.mode = mode
        self.rounds = rounds
        self.seconds = seconds
        self.running = True
        self.phase = "ready"
        self.round = 0
        self.phase_until = time.time() + self.READY_S
        self.results = []          # me/other：每遍的结果；quiet：每次被叫到的记录
        self.started = time.time()
        self.stop_flag = threading.Event()
        self._cur = None
        self._lock = threading.Lock()
        self.summary = None
        threading.Thread(target=self._run, name="wake-test", daemon=True).start()

    def on_result(self, info):
        rec = {"ok": bool(info.get("ok")), "score": info.get("score"), "path": info.get("path") or "kws",
               "kw": (info.get("kw") or "").replace("_", " "), "t": round(time.time() - self.started, 1),
               "threshold": info.get("threshold"), "enrolled": info.get("enrolled", True)}
        with self._lock:
            if self.mode == "quiet":
                self.results.append(rec)
            elif self.phase in ("speak", "grace") and self._cur is None:
                self._cur = rec

    def _sleep_until(self, t):
        while time.time() < t:
            if self.stop_flag.wait(min(0.1, max(0.0, t - time.time()))):
                return False
        return True

    def _run(self):
        eng = _engine()
        try:
            eng.cool_until = 0
            eng.tap = self.on_result
            if self.mode == "quiet":
                self.phase = "listen"
                self.phase_until = time.time() + self.seconds
                self._sleep_until(self.phase_until)
            else:
                for i in range(self.rounds):
                    self.round = i + 1
                    self.phase, self.phase_until = "ready", time.time() + self.READY_S
                    if not self._sleep_until(self.phase_until):
                        break
                    with self._lock:
                        self._cur = None
                    self.phase, self.phase_until = "speak", time.time() + self.SPEAK_S
                    if not self._sleep_until(self.phase_until):
                        break
                    # 第二条路要等说完才认，多等一会儿；第一条路已经有结果就不用等
                    if self._cur is None:
                        self.phase, self.phase_until = "grace", time.time() + self.GRACE_S
                        t_end = self.phase_until
                        while time.time() < t_end and self._cur is None and not self.stop_flag.is_set():
                            time.sleep(0.1)
                    with self._lock:
                        r = self._cur or {"ok": False, "score": None, "path": None, "miss": True}
                        r["round"] = i + 1
                        self.results.append(r)
                        self._cur = None
                    self.phase, self.phase_until = "result", time.time() + self.GAP_S
                    if not self._sleep_until(self.phase_until):
                        break
        finally:
            if eng is not None and eng.tap == self.on_result:
                eng.tap = None
                eng.cool_until = time.time() + 1.5   # 刚测完别马上被自己的尾音叫醒
            self.summary = self._summarize()
            self.phase = "done"
            self.running = False

    def _summarize(self):
        rs = self.results
        scores = [r["score"] for r in rs if r.get("score") is not None]
        if self.mode == "quiet":
            mins = max(1e-6, (time.time() - self.started) / 60)
            return {"minutes": round(mins, 1), "heard": len(rs), "passed": sum(1 for r in rs if r["ok"]),
                    "per_hour": round(sum(1 for r in rs if r["ok"]) * 60 / mins, 1) if mins >= 1 else None,  # 不到 1 分钟折算没意义
                    "max_score": round(max(scores), 3) if scores else None}
        n = len(rs)
        hits = [r for r in rs if not r.get("miss")]
        return {"rounds": n, "hits": len(hits), "passed": sum(1 for r in hits if r["ok"]),
                "wake_rate": round(len(hits) / n, 3) if n else None,
                "pass_rate": round(sum(1 for r in hits if r["ok"]) / len(hits), 3) if hits else None,
                "kws": sum(1 for r in hits if r.get("path") == "kws"), "asr": sum(1 for r in hits if r.get("path") == "asr"),
                "avg_score": round(sum(scores) / len(scores), 3) if scores else None,
                "min_score": round(min(scores), 3) if scores else None,
                "max_score": round(max(scores), 3) if scores else None}

    def public(self):
        with self._lock:
            return {"mode": self.mode, "running": self.running, "phase": self.phase, "round": self.round,
                    "rounds": self.rounds, "seconds": self.seconds,
                    "left_ms": max(0, int((self.phase_until - time.time()) * 1000)),
                    "elapsed_s": round(time.time() - self.started, 1),
                    "results": self.results[-40:], "summary": self.summary,
                    "live": self._summarize() if self.running else None}

    def stop(self):
        self.stop_flag.set()


_test = None


def do_test_start(data):
    global _test
    eng = _engine()
    if eng is None:
        return 503, {"error": "唤醒没在运行（模型没下好，或者语音服务刚启动）"}
    if enroll.sess:
        return 409, {"error": "正在录声纹，录完再测"}
    if _test and _test.running:
        return 409, {"error": "已经在测了"}
    mode = data.get("mode")
    if mode not in ("me", "other", "quiet"):
        return 400, {"error": "不认识的测试"}
    try:
        rounds = max(3, min(20, int(data.get("rounds") or 10)))
        seconds = max(30, min(600, int(data.get("seconds") or 180)))
    except (TypeError, ValueError):
        return 400, {"error": "参数不对"}
    _test = Test(mode, rounds, seconds)
    t = threading.Timer(MAX_TEST_S, _test.stop)   # 兜底：怎么样都不会一直拦着唤醒
    t.daemon = True
    t.start()
    return 200, _test.public()


# ---------------------------------------------------------------- 路由
def handle(method, path, query, body):
    act = path[len("/assist/"):].strip("/")
    data = {}
    if method == "POST":
        try:
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001
            data = {}
    if method == "GET":
        if act == "state":
            return _json(200, state())
        if act == "namecheck":
            return _json(200, _check.public() if _check else {})
        if act == "test":
            return _json(200, _test.public() if _test else {})
        return _json(404, {"error": "没有这个接口"})
    if act == "name":
        return _json(*do_rename(data))
    if act == "namecheck":
        return _json(*do_namecheck(data))
    if act == "test/start":
        return _json(*do_test_start(data))
    if act == "test/stop":
        if _test:
            _test.stop()
        return _json(200, _test.public() if _test else {})
    return _json(404, {"error": "没有这个接口"})


def register(hub):
    hub.route("GET", "/assist/", handle)
    hub.route("POST", "/assist/", handle)
