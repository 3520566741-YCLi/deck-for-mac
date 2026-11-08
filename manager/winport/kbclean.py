"""键盘清洁（Windows 版，11-08 第二步）：锁住键盘（可选连鼠标一起锁），擦完解锁。

和 Mac 版 kbclean.py 的函数名、参数、返回的数据结构一样（status / start / stop / extend / open_settings）。
Mac 上拦按键的是 DeckHands 小程序；Windows 上在 Deck 进程里开一条线程，用系统的低级键盘钩子（WH_KEYBOARD_LL）吞掉所有按键，
勾了「连鼠标一起锁」再加一个低级鼠标钩子（WH_MOUSE_LL）。不需要管理员，也不用另外授权。

怎么解锁（任何一种都行）：
- 倒计时结束自动解锁；
- 在 Deck 页面点「解锁」，或对 Vesper 说「解锁键盘」；
- 按住 Esc 3 秒；
- 按 Ctrl+Alt+Del 或锁屏：Ctrl+Alt+Del 是系统保留的，任何程序都挡不住，按下去切到安全桌面，Deck 发现后马上解锁。
兜底：
- 另一条看门狗线程，到点 3 秒后还锁着、或者钩子线程 6 秒没心跳，就直接拆掉钩子；
- 钩子回调卡住超过系统时限，Windows 会自己跳过它（按键照常生效）；Deck 进程退出，钩子也跟着没了。
黑屏：Mac 版靠原生小程序画全屏面板，Windows 版暂时不做，传 black=True 只会在结果里说明一下。
在 Linux 上（DECK_OS=win 开发）也能导入，status 返回空闲，start 返回错误。
"""
import ctypes
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
DURATIONS = (30, 60, 90, 120, 300)
MAX_TOTAL = 600
MAX_MOUSE = 120
MIN_SECONDS = 10  # 测试时可以改小
ESC_HOLD = 3.0  # 按住 Esc 多少秒解锁
BEAT_STALE = 6
EXTEND_STEP = 30
UNLOCK_WAYS = ["按住 Esc 3 秒", "倒计时结束自动解锁", "在这里点「解锁」，或对 Vesper 说「解锁键盘」",
               "按 Ctrl+Alt+Del 或锁屏时自动解锁（系统保留的组合键，任何程序都挡不住）"]

_lock = threading.RLock()  # status() 可能在 start() 持锁时被调用
# 当前这次清洁的状态（只在内存里：Deck 进程退出，钩子也就没了）
_S = {"state": "idle", "reason": None, "at": None, "error": None, "error_at": None}

if IS_WIN:
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)  # 自己的一份，不改全局 windll 的函数签名
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    _u32.SetWindowsHookExW.restype = ctypes.c_void_p
    _u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _u32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _u32.CallNextHookEx.restype = LRESULT
    _u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _u32.GetMessageW.restype = wintypes.BOOL
    _u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.PostThreadMessageW.restype = wintypes.BOOL
    _u32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    _u32.SetTimer.restype = ctypes.c_size_t
    _u32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    _u32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _u32.OpenInputDesktop.restype = ctypes.c_void_p
    _u32.CloseDesktop.argtypes = [ctypes.c_void_p]
    _k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _k32.GetModuleHandleW.restype = wintypes.HMODULE
    _k32.GetCurrentThreadId.restype = wintypes.DWORD

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_TIMER, WM_QUIT = 0x113, 0x12
VK_ESCAPE = 0x1B


class _Session:
    """一次清洁：一条钩子线程（装钩子 + 消息循环）+ 一条看门狗线程。"""

    def __init__(self, seconds, mouse):
        now = time.time()
        self.started = now
        self.until = now + seconds
        self.max = now + (MAX_MOUSE if mouse else MAX_TOTAL)
        self.mouse = mouse
        self.tid = None
        self.hooks = []
        self.beat = now
        self.esc_down = None
        self.reason = None
        self.ready = threading.Event()
        self.ended = threading.Event()
        self.error = None
        self._procs = []

    # ---- 钩子回调：要快，只记一下、吞掉按键 ----
    def _kb(self, code, wparam, lparam):
        try:
            if code < 0:
                return _u32.CallNextHookEx(None, code, wparam, lparam)
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if k.vkCode == VK_ESCAPE:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if self.esc_down is None:
                        self.esc_down = time.time()
                    elif time.time() - self.esc_down >= ESC_HOLD:
                        self.finish("esc")
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    self.esc_down = None
            elif wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self.esc_down = None  # 按住 Esc 的时候碰了别的键，重新算
            return 1  # 吞掉
        except Exception:  # noqa: BLE001  回调里出错就放行，绝不把键盘锁死
            return _u32.CallNextHookEx(None, code, wparam, lparam)

    def _ms(self, code, wparam, lparam):
        if code < 0:
            return _u32.CallNextHookEx(None, code, wparam, lparam)
        return 1

    def finish(self, reason):
        """请钩子线程结束（拆钩子在它自己的线程里做）。"""
        if self.reason is None:
            self.reason = reason
        if self.tid:
            _u32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)

    def _unhook(self):
        for h in self.hooks:
            try:
                _u32.UnhookWindowsHookEx(h)
            except OSError:
                pass
        self.hooks = []

    def _secure_desktop(self):
        """按了 Ctrl+Alt+Del 或锁屏时，输入桌面换成了系统的安全桌面，普通程序打不开它。"""
        h = _u32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not h:
            return True
        _u32.CloseDesktop(h)
        return False

    def run(self):
        self.tid = _k32.GetCurrentThreadId()
        mod = _k32.GetModuleHandleW(None)
        self._procs = [HOOKPROC(self._kb)]  # 留着引用，免得被回收
        h = _u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._procs[0], mod, 0)
        if not h:
            self.error = "没能装上键盘钩子（错误 %d）" % ctypes.get_last_error()
            self.ready.set()
            self.ended.set()
            return
        self.hooks.append(h)
        if self.mouse:
            self._procs.append(HOOKPROC(self._ms))
            hm = _u32.SetWindowsHookExW(WH_MOUSE_LL, self._procs[1], mod, 0)
            if hm:
                self.hooks.append(hm)
        timer = _u32.SetTimer(None, 0, 200, None)
        # 一开始就打不开输入桌面（比如在服务里跑）就不靠它判断锁屏，免得一锁就解
        watch_desktop = not self._secure_desktop()
        self.ready.set()
        msg = wintypes.MSG()
        try:
            while True:
                r = _u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r in (0, -1):
                    break
                if msg.message == WM_TIMER:
                    now = time.time()
                    self.beat = now
                    if now >= min(self.until, self.max):
                        self.finish("timer")
                    elif self.esc_down is not None and now - self.esc_down >= ESC_HOLD:
                        self.finish("esc")
                    elif watch_desktop and self._secure_desktop():
                        self.finish("screen locked")
        finally:
            if timer:
                _u32.KillTimer(None, timer)
            self._unhook()
            self.ended.set()


_cur = {"s": None}


def _watchdog(s):
    """服务端兜底：每秒看一眼。过了截止时间 3 秒还锁着，或者钩子线程 6 秒没心跳，就直接拆钩子。"""
    while not s.ended.wait(1):
        now = time.time()
        late = now > min(s.until, s.max) + 3
        stale = now - s.beat > BEAT_STALE
        if late or stale:
            print("键盘清洁：看门狗解锁（%s）" % ("过了截止时间" if late else "钩子线程没心跳"), flush=True)
            s.reason = s.reason or "watchdog"
            s.finish(s.reason)
            if not s.ended.wait(2):
                s._unhook()  # 钩子线程卡死了：从这里拆（低级钩子可以从别的线程拆）
                s.ended.set()
            break
    _end(s)


def _end(s):
    with _lock:
        if _cur["s"] is s:
            _cur["s"] = None
            _S.update(state="idle", reason=s.reason or "timer", at=time.time())


def status():
    out = {"installed": IS_WIN, "durations": list(DURATIONS), "now": time.time(), "unlock_ways": UNLOCK_WAYS, "black_supported": False}
    s = _cur["s"]
    if s and not s.ended.is_set():
        out.update(state="locked", until=min(s.until, s.max), started=s.started, black=False, mouse=s.mouse, max=s.max)
        return out
    if s and s.ended.is_set():
        _end(s)
    if _S.get("error") and (_S.get("error_at") or 0) >= (_S.get("at") or 0):
        out.update(state="idle", error=_S["error"], error_at=_S["error_at"])
        return out
    out.update(state="idle", last_reason=_S.get("reason"), last_at=_S.get("at"))
    return out


def start(seconds=90, black=True, mouse=False, skin=None, theme=None):
    if not IS_WIN:
        return dict(status(), error="键盘清洁只在 Windows 上能用")
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 90
    seconds = max(MIN_SECONDS, min(MAX_MOUSE if mouse else MAX_TOTAL, seconds))
    with _lock:
        cur = status()
        if cur["state"] == "locked":
            return dict(cur, note="已经锁着了")
        s = _Session(seconds, bool(mouse))
        _cur["s"] = s
        _S.update(error=None, error_at=None)
    threading.Thread(target=s.run, name="deck-kbclean", daemon=True).start()
    if not s.ready.wait(5) or s.error:
        s.finish("error")
        with _lock:
            if _cur["s"] is s:
                _cur["s"] = None
            _S.update(error=s.error or "键盘钩子 5 秒内没装好，键盘没锁", error_at=time.time())
        return dict(status(), error=_S["error"])
    threading.Thread(target=_watchdog, args=(s,), name="deck-kbclean-watchdog", daemon=True).start()
    out = status()
    if black:
        out["note"] = "Windows 版暂时没有黑屏，只锁了键盘" + ("和鼠标" if mouse else "")
    return out


def stop():
    s = _cur["s"]
    if not s or s.ended.is_set():
        return status()
    s.finish("deck")
    if not s.ended.wait(2):
        s._unhook()
        s.ended.set()
    _end(s)
    return dict(status(), unlocked=True)


def extend():
    s = _cur["s"]
    if not s or s.ended.is_set():
        return status()
    s.until = min(s.max, s.until + EXTEND_STEP)
    return status()


def open_settings():
    return {"ok": True, "note": "Windows 版不需要额外授权"}
