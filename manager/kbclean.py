"""键盘清洁：锁住键盘（可选黑屏、连触控板一起锁），擦完解锁。

真正拦按键的是 DeckHands 的清洁模式（manager/hands/CleanMode.swift），这里只负责：
- 启动：`open -n DeckHands.app --args --clean ...`，另起一个进程，沿用 DeckHands 已有的辅助功能权限；
- 状态：读 DeckHands 写的 ~/Library/Application Support/Deck/clean.json，并核对那个进程还活着；
- 解锁 / 加时：给那个进程发 SIGTERM / SIGUSR1（发之前核对它确实是 DeckHands 的清洁进程，不会误杀别的）；
- 兜底：服务端另有一条线程，到点还没解锁就直接结束那个进程（进程一退出，系统自动撤掉拦截）。
"""
import json
import os
import signal
import subprocess
import threading
import time

APP_CANDIDATES = [os.path.expanduser("~/Applications/DeckHands.app"), "/Applications/DeckHands.app"]
STATUS = os.path.expanduser("~/Library/Application Support/Deck/clean.json")
DURATIONS = (30, 60, 90, 120, 300)
MAX_TOTAL = 600
_lock = threading.Lock()
_watch = {"pid": None}
# 原生锁屏面板的样子跟 Deck 走：soft = 新拟态，glass = 液态玻璃；theme = light / dark。
# Vesper 语音开的锁没有页面传参，就用页面上一次传来的
_look = {"skin": "soft", "theme": None}


def _app():
    for p in APP_CANDIDATES:
        if os.path.isdir(p):
            return p
    return None


def _read():
    try:
        with open(STATUS) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_clean_proc(pid):
    """pid 必须是活着的 DeckHands，而且命令行带 --clean，才算我们的清洁进程。"""
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "DeckHands.app/Contents/MacOS/DeckHands" in out and "--clean" in out


def status():
    d = _read()
    st = d.get("state")
    out = {"installed": bool(_app()), "durations": list(DURATIONS), "now": time.time()}
    if st == "locked":
        if _is_clean_proc(d.get("pid")):
            out.update(state="locked", until=d.get("until"), started=d.get("started"), black=d.get("black"),
                       mouse=d.get("mouse"), max=d.get("max"))
            return out
        out.update(state="idle", last="进程已经退出")
        return out
    if st == "error":
        out.update(state="idle", error=d.get("error"), error_at=d.get("at"))
        return out
    out.update(state="idle", last_reason=d.get("reason"), last_at=d.get("at"))
    return out


def _kill(pid, why):
    if not _is_clean_proc(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(20):
        time.sleep(0.1)
        if not _is_clean_proc(pid):
            return True
    try:  # 不理 SIGTERM（主线程卡住）就强制结束；进程没了，系统自然撤掉拦截
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    print("键盘清洁：强制结束 %s（%s）" % (pid, why), flush=True)
    return True


def _mark_done(pid, reason):
    """进程是被我们强制结束的，它自己来不及写状态：替它写上 done，界面才知道为什么解锁。"""
    d = _read()
    if d.get("pid") == pid and d.get("state") == "locked":
        tmp = STATUS + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"state": "done", "reason": reason, "pid": pid, "at": time.time()}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, STATUS)
        except OSError:
            pass


BEAT_STALE = 6  # 清洁进程每 2 秒写一次心跳；6 秒没动静就当它卡死了


def _watchdog(pid):
    """服务端兜底：每秒看一眼。过了截止时间 8 秒还锁着，或者心跳停了（主线程卡死 / 被暂停），就结束进程。"""
    while True:
        time.sleep(1)
        d = _read()
        if d.get("pid") != pid or d.get("state") != "locked" or not _is_clean_proc(pid):
            break
        until = float(d.get("until") or 0)
        started = float(d.get("started") or time.time())
        if time.time() > min(until, started + MAX_TOTAL) + 8:
            _kill(pid, "过了截止时间还没解锁")
            _mark_done(pid, "watchdog")
            break
        if d.get("beat") and time.time() - float(d.get("at") or time.time()) > BEAT_STALE:
            _kill(pid, "心跳停了 %d 秒，当它卡死" % BEAT_STALE)
            _mark_done(pid, "watchdog")
            break
    with _lock:
        if _watch["pid"] == pid:
            _watch["pid"] = None


def start(seconds=90, black=True, mouse=False, skin=None, theme=None):
    if skin in ("soft", "glass"):
        _look["skin"] = skin
    if theme in ("light", "dark"):
        _look["theme"] = theme
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 90
    seconds = max(10, min(120 if mouse else MAX_TOTAL, seconds))
    with _lock:
        cur = status()
        if cur["state"] == "locked":
            return dict(cur, note="已经锁着了")
        app = _app()
        if not app:
            return {"error": "没找到 DeckHands.app（键盘清洁靠它拦按键）。在 Mac 上运行 manager/hands/build.sh 装一下。"}
        try:  # 清掉上一次的状态，免得把旧的 locked/error 当成这一次的结果
            os.remove(STATUS)
        except OSError:
            pass
        args = ["open", "-n", app, "--args", "--clean", "--seconds", str(seconds),
                "--black", "1" if black else "0", "--mouse", "1" if mouse else "0", "--skin", _look["skin"]]
        if _look["theme"]:
            args += ["--theme", _look["theme"]]
        try:
            subprocess.run(args, capture_output=True, timeout=8, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            return {"error": "没能启动 DeckHands：%s" % e}
    for _ in range(50):  # 最多等 5 秒，等它装好拦截或报错
        time.sleep(0.1)
        d = _read()
        if d.get("state") == "error":
            return dict(status(), error=d.get("error"))
        if d.get("state") == "locked" and _is_clean_proc(d.get("pid")):
            pid = d["pid"]
            with _lock:
                if _watch["pid"] != pid:
                    _watch["pid"] = pid
                    threading.Thread(target=_watchdog, args=(pid,), daemon=True).start()
            return status()
    return dict(status(), error="DeckHands 5 秒内没反应，键盘没锁。可以在菜单栏 ✋ 里看看它是不是在运行。")


def stop():
    d = _read()
    if d.get("state") != "locked":
        return status()
    _kill(d.get("pid"), "Deck 解锁")
    time.sleep(0.15)
    return dict(status(), unlocked=True)


def extend():
    d = _read()
    pid = d.get("pid")
    if d.get("state") != "locked" or not _is_clean_proc(pid):
        return status()
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError:
        pass
    time.sleep(0.2)
    return status()


def open_settings():
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"], timeout=5)
    return {"ok": True}
