"""Vesper 的「手」（Windows 版，11-08 第四步）：读屏幕元素、点、打字、按快捷键、滚动、打开应用。

Mac 上动手的是单独的 DeckHands.app（辅助功能 API），hands.py 经 Unix socket 跟它说 JSON；
Windows 上不另起程序，就在 Deck 进程里：hands.py 在 Windows 上把同样的请求交给这里的 call(req)，
**命令、字段、返回的结构都和 DeckHands.swift 一样**（status / arm / stop / elements / press / type / key / scroll，外加 open）。

怎么做：
- 读元素：系统的 UI 自动化（UI Automation，讲述人和屏幕阅读器用的同一套接口），经 comtypes 调用。
  读前台窗口（或指定应用）里的按钮、链接、菜单、输入框…，给出 role / label / frame / enabled，密码框标 secure、永远不读它的内容。
  应用身份用 exe 名（chrome.exe），相当于 Mac 的 bundle id。
- 点：优先用 UI 自动化的「调用」（Invoke / Toggle / 选中 / 展开），不行再把鼠标移过去点（SendInput）。
  点之前核对元素还在原处（屏幕变了就不点），并在目标四周闪一下框。
- 打字：SendInput 的 Unicode 输入，一个字一个字发，每个字之间看一眼急停；焦点在密码框里就拒绝。
- 按键：ctrl / opt(=Alt) / shift / win + 主键。hands.py 在 Windows 上已经把 cmd 换成 ctrl（Mac 的 ⌘C 就是 Windows 的 Ctrl+C）。
- 所有 UI 自动化的事都在一条专门的线程里做（多线程套间），和 DeckHands 的单线程工作队列一样；stop / status 不排队，立刻生效。

自己守住的底线（和 DeckHands 一样）：
- 「允许 Vesper 操作电脑」开关（存在 %APPDATA%\\Deck\\hands.json，**默认关**），关着时什么都不做。开关在 Deck 设置页 / 托盘菜单里。
- 急停：Deck 发 stop（说「停」、页面停止按钮）、全局快捷键 Ctrl+Alt+Esc（备用 Ctrl+Alt+.）、动手期间连按 3 次 Esc。
  停下后拒绝一切动作，直到下一次新的提问（Deck 发 arm）。
- 不往密码框里打字，也不读密码框的内容；打的字不写进日志（只记字数）。
- 碰不到「以管理员身份运行」的窗口（Windows 的 UIPI 会拦），直接说清楚，不假装点了。
不需要额外授权（没有 Mac 的「辅助功能」权限这一步），所以 status 的 trusted 恒为真（Windows 上）。
在 Linux 上（DECK_OS=win 开发）也能导入：status 能用，动作都返回「只能在 Windows 上用」。
"""
import ctypes
import importlib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request
import uuid

IS_WIN = sys.platform == "win32"
NOT_WIN = "电脑操控只能在 Windows 上用"
HOTKEYS = (("Ctrl+Alt+Esc", 0x1B), ("Ctrl+Alt+.", 0xBE))  # 急停快捷键：(显示, 虚拟键码)，修饰键都是 Ctrl+Alt
HOTKEY_TEXT = HOTKEYS[0][0]
SNAP_TTL = 180
MAX_TEXT = 2000
MARK = 0xDEC4  # 我们自己发的输入事件带这个标记（dwExtraInfo），连按 Esc 的监听不把它算进去

_lock = threading.Lock()
_S = {"stopped": False, "acting": False, "hotkeys": [], "uia_error": None, "enabled": None, "enabled_mtime": None}
_snaps = {}
_snap_order = []
ON_STOP = []  # 急停时还要通知谁（server.py 可以登记 CHAT_STOP.set，让这一轮对话也停下）

if IS_WIN:
    from ctypes import wintypes

    from winport import appctl

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTU(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTU)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    for _n, _a, _r in (
        ("SendInput", [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int], wintypes.UINT),
        ("SetCursorPos", [ctypes.c_int, ctypes.c_int], wintypes.BOOL),
        ("GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        ("WindowFromPoint", [wintypes.POINT], wintypes.HWND),
        ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        ("GetForegroundWindow", [], wintypes.HWND),
        ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ("GetGUIThreadInfo", [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)], wintypes.BOOL),
        ("GetWindowLongW", [wintypes.HWND, ctypes.c_int], wintypes.LONG),
        ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
        ("RegisterHotKey", [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        ("GetMessageW", [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        ("PeekMessageW", [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        ("TranslateMessage", [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
        ("DispatchMessageW", [ctypes.POINTER(wintypes.MSG)], LRESULT),
        ("PostThreadMessageW", [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        ("SetWindowsHookExW", [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD], wintypes.HHOOK),
        ("CallNextHookEx", [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM], LRESULT),
        ("UnhookWindowsHookEx", [wintypes.HHOOK], wintypes.BOOL),
        ("CreateWindowExW", [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID],
         wintypes.HWND),
        ("DestroyWindow", [wintypes.HWND], wintypes.BOOL),
        ("SetLayeredWindowAttributes", [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD], wintypes.BOOL),
        ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
    ):
        _f = getattr(_u32, _n)
        _f.argtypes, _f.restype = _a, _r
    _k32.GetCurrentThreadId.restype = wintypes.DWORD
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _k32.GetModuleHandleW.restype = wintypes.HMODULE
    _adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]

# ---------------------------------------------------------------- UI 自动化的编号（系统接口里固定不变的数）

P_RUNTIME, P_RECT, P_TYPE, P_LTYPE, P_NAME, P_FOCUSABLE, P_ENABLED, P_HELP, P_PASSWORD, P_OFFSCREEN = (
    30000, 30001, 30003, 30004, 30005, 30009, 30010, 30013, 30019, 30022)
P_HAS_INVOKE, P_HAS_TEXT, P_HAS_VALUE, P_VALUE, P_READONLY = 30031, 30040, 30043, 30045, 30046
SCOPE_CHILDREN, SCOPE_DESC = 2, 4
PAT_INVOKE, PAT_VALUE, PAT_EXPAND, PAT_SELITEM, PAT_TEXT, PAT_TOGGLE, PAT_LEGACY = 10000, 10002, 10005, 10010, 10014, 10015, 10018
CT = {50000: "Button", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit", 50005: "Hyperlink", 50007: "ListItem",
      50010: "MenuBar", 50011: "MenuItem", 50013: "RadioButton", 50015: "Slider", 50016: "Spinner", 50019: "TabItem",
      50024: "TreeItem", 50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50035: "HeaderItem"}
CT_ID = {v: k for k, v in CT.items()}
# 能点 / 能输入的控件（相当于 DeckHands 的 ACTIONABLE）；另外任何支持「调用」的元素也算
ACTIONABLE = [k for k, v in CT.items() if v not in ("MenuBar", "Document")]
TEXT_TYPES = {CT_ID["Edit"], CT_ID["ComboBox"], CT_ID["Document"]}
CACHE_PROPS = (P_RUNTIME, P_RECT, P_TYPE, P_LTYPE, P_NAME, P_FOCUSABLE, P_ENABLED, P_HELP, P_PASSWORD, P_OFFSCREEN,
               P_HAS_INVOKE, P_HAS_TEXT, P_HAS_VALUE, P_VALUE, P_READONLY)


# ---------------------------------------------------------------- 开关、急停、日志

def _state_file():
    import deckos  # noqa: PLC0415  （测试会先改 APPDATA 再调用，所以每次现算路径）
    return deckos.support("Deck", "hands.json")


def enabled():
    """「允许 Vesper 操作电脑」开关。默认关；文件被别处改了（设置页 / 托盘）会重新读。"""
    p = _state_file()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return False
    with _lock:
        if _S["enabled_mtime"] == (p, mt):
            return bool(_S["enabled"])
    try:
        with open(p, encoding="utf-8") as f:
            v = bool(json.load(f).get("enabled"))
    except (OSError, ValueError, AttributeError):
        v = False
    with _lock:
        _S.update(enabled=v, enabled_mtime=(p, mt))
    return v


def set_enabled(on):
    """设置页的开关 / 托盘菜单调用。返回 status()。"""
    p = _state_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"enabled": bool(on), "at": int(time.time())}, f)
    os.replace(tmp, p)
    with _lock:
        _S.update(enabled=None, enabled_mtime=None)
    _log("enabled=%s" % bool(on))
    return status()


def _log_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Deck", "logs", "deck-hands.log")


def _log(s):
    try:
        p = _log_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), s))
    except OSError:
        pass


def is_stopped():
    with _lock:
        return _S["stopped"]


def _set_stopped(v):
    with _lock:
        _S["stopped"] = bool(v)


def on_stop(fn):
    """登记一个急停时要调用的函数（比如 server.py 的 CHAT_STOP.set）。"""
    if fn not in ON_STOP:
        ON_STOP.append(fn)


def stop_now(why):
    _set_stopped(True)
    _log("STOP (%s)" % why)
    if why == "deck":
        return
    # 自己这边发起的急停（快捷键 / 连按 Esc）也让这一轮对话停下：通知登记过的人，再照 Mac 版告诉语音服务
    for fn in list(ON_STOP):
        try:
            fn()
        except Exception:  # noqa: BLE001  通知失败不影响急停本身
            pass

    def tell_voice():
        try:
            req = urllib.request.Request("http://127.0.0.1:8778/stop", data=b'{"source":"hands"}', method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).close()
        except Exception:  # noqa: BLE001  语音服务没开就算了
            pass
    threading.Thread(target=tell_voice, daemon=True).start()


def _guard():
    if not IS_WIN:
        return NOT_WIN
    if not enabled():
        return "Deck 设置里「允许 Vesper 操作电脑」关着"
    if is_stopped():
        return "刚被急停了，这一轮不再动"
    return None


# ---------------------------------------------------------------- 急停快捷键（自己的线程，收系统消息）

_hk = {"thread": None, "tid": 0, "hook": None, "proc": None, "esc": []}
WM_HOTKEY, WM_APP_HOOK_ON, WM_APP_HOOK_OFF = 0x0312, 0x8001, 0x8002


def _hook_proc(code, wparam, lparam):
    try:
        if code == 0 and wparam in (0x100, 0x104):  # WM_KEYDOWN / WM_SYSKEYDOWN
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if k.vkCode == 0x1B and not (k.flags & 0x10) and k.dwExtraInfo != MARK:  # Esc，不是程序发的
                now = time.time()
                _hk["esc"] = [t for t in _hk["esc"] if now - t < 1.2] + [now]
                if len(_hk["esc"]) >= 3:
                    _hk["esc"] = []
                    threading.Thread(target=stop_now, args=("Esc×3",), daemon=True).start()
    except Exception:  # noqa: BLE001  钩子里绝不能出错卡住键盘
        pass
    return _u32.CallNextHookEx(None, code, wparam, lparam)


def _hotkey_loop(ready):
    msg = wintypes.MSG()
    _u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 先建好消息队列，别的线程才能给它发消息
    _hk["tid"] = _k32.GetCurrentThreadId()
    got = []
    for i, (label, vk) in enumerate(HOTKEYS):
        if _u32.RegisterHotKey(None, 0xD0C0 + i, 0x1 | 0x2 | 0x4000, vk):  # MOD_ALT | MOD_CONTROL | MOD_NOREPEAT
            got.append(label)
        else:
            _log("hotkey %s taken" % label)
    with _lock:
        _S["hotkeys"] = got
    ready.set()
    while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            stop_now("hotkey")
        elif msg.message == WM_APP_HOOK_ON and not _hk["hook"]:
            _hk["proc"] = HOOKPROC(_hook_proc)
            _hk["hook"] = _u32.SetWindowsHookExW(13, _hk["proc"], _k32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
        elif msg.message == WM_APP_HOOK_OFF and _hk["hook"]:
            _u32.UnhookWindowsHookEx(_hk["hook"])
            _hk["hook"] = None
        else:
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))


def start():
    """起急停快捷键的线程（第一次 call 时自动起；Deck 启动时也可以先调一次）。"""
    if not IS_WIN:
        return
    with _lock:
        if _hk["thread"]:
            return
        ready = threading.Event()
        _hk["thread"] = threading.Thread(target=_hotkey_loop, args=(ready,), name="deck-hands-hotkey", daemon=True)
        _hk["thread"].start()
    ready.wait(2)


def _acting(on):
    """动手期间才听「连按 3 次 Esc」（低级键盘钩子只在这几秒里装着）。"""
    with _lock:
        _S["acting"] = on
    if IS_WIN and _hk["tid"]:
        _u32.PostThreadMessageW(_hk["tid"], WM_APP_HOOK_ON if on else WM_APP_HOOK_OFF, 0, 0)


# ---------------------------------------------------------------- 工作线程（UI 自动化都在这里）

_jobs = queue.Queue()
_W = {"thread": None, "busy_since": None, "uia": None, "mod": None}


def _com_init():
    """当前线程进入多线程套间（MTA）：UI 自动化推荐这样用，元素也能交给别的 MTA 线程（点按钮的小线程）用。"""
    if "comtypes" not in sys.modules:
        sys.coinit_flags = 0  # comtypes 第一次导入时按这个初始化（0 = COINIT_MULTITHREADED）
        try:
            importlib.import_module("comtypes")
        finally:
            del sys.coinit_flags
    else:
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # 已经初始化过（或别的模式）也无妨


def _dpi_aware():
    """这条线程按物理像素算坐标（UI 自动化给的框、鼠标位置、屏幕大小用同一种单位）。Win10 1607 之前没有就算了。"""
    try:
        _u32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        _u32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        _u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def _uia():
    if _W["uia"] is None:
        import comtypes.client  # noqa: PLC0415
        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        _W["mod"] = mod
        _W["uia"] = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
    return _W["uia"]


def _worker():
    try:
        _com_init()
    except ImportError as e:
        with _lock:
            _S["uia_error"] = "缺少 comtypes（%s）" % e
    except Exception as e:  # noqa: BLE001
        with _lock:
            _S["uia_error"] = "COM 初始化失败：%s" % e
    _dpi_aware()
    while True:
        fn, box, done = _jobs.get()
        _W["busy_since"] = time.time()
        try:
            box["r"] = fn()
        except ImportError as e:
            with _lock:
                _S["uia_error"] = "缺少 comtypes（%s）" % e
            box["r"] = {"ok": False, "error": "电脑操控缺少组件 comtypes，重新安装 Deck 试试"}
        except Exception as e:  # noqa: BLE001  一个动作出错不能把线程弄死
            box["r"] = {"ok": False, "error": "电脑操控出错：%s" % str(e)[:160]}
        finally:
            _W["busy_since"] = None
            done.set()


def _run(fn, timeout):
    with _lock:
        stuck = _W["busy_since"] and time.time() - _W["busy_since"] > 20
        if not _W["thread"] or stuck:
            # 上一个动作卡在别的程序里（比如它没响应）：另起一条工作线程，卡住的那条做完自己会空着
            _W["thread"] = threading.Thread(target=_worker, name="deck-hands", daemon=True)
            _W["thread"].start()
    box, done = {}, threading.Event()
    _jobs.put((fn, box, done))
    if not done.wait(timeout):
        return {"ok": False, "error": "电脑操控 %d 秒内没做完（那个程序可能没响应）" % int(timeout)}
    r = box.get("r")
    # 用 is None 判断：读到空文字（""）是正常结果，不能当成「没有返回」（11-08 CI 实测全选删光后误报）
    return {"ok": False, "error": "电脑操控没有返回"} if r is None else r


def _bg(fn, wait=2.0):
    """在一条临时的 MTA 小线程里调用（「调用」按钮会打开模态对话框时，Win32 的实现会一直等到对话框关上）。
    wait 秒内没返回就当已经点下去了。"""
    box = {}

    def run():
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            fn()
            box["ok"] = True
        except Exception as e:  # noqa: BLE001
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(wait)
    if "err" in box:
        raise box["err"]
    return True


# ---------------------------------------------------------------- 小工具

def _clip(s, n):
    one = str(s or "").replace("\r", " ").replace("\n", " ").strip()
    return one if len(one) <= n else one[:n] + "…"


def _me_elevated():
    return _elevated_handle(_k32.GetCurrentProcess())


def _elevated_handle(h):
    tok = wintypes.HANDLE()
    if not _adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
        return None
    try:
        v, n = wintypes.DWORD(), wintypes.DWORD()
        if not _adv.GetTokenInformation(tok, 20, ctypes.byref(v), 4, ctypes.byref(n)):  # TokenElevation
            return None
        return bool(v.value)
    finally:
        _k32.CloseHandle(tok)


def _blocked(pid):
    """目标是管理员权限运行、而 Deck 不是：系统不让普通程序往里点和打字。返回原因或 None。"""
    if not pid or _me_elevated():
        return None
    h = _k32.OpenProcess(0x1000, False, int(pid))
    el = None
    if h:
        try:
            el = _elevated_handle(h)
        finally:
            _k32.CloseHandle(h)
    if el is None and h:
        el = True  # 连令牌都不让读：多半是管理员或受保护的进程
    if el:
        return "这个窗口是以管理员身份运行的，Windows 不让 Vesper 碰它"
    return None


def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    return _u32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _ki(vk=0, scan=0, flags=0):
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, MARK)
    return i


def _mi(flags, data=0):
    i = INPUT(type=0)
    i.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, MARK)
    return i


def _click_at(x, y):
    _u32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    _send([_mi(0x0002)])  # LEFTDOWN
    time.sleep(0.04)
    _send([_mi(0x0004)])  # LEFTUP


def _virtual_screen():
    x, y = _u32.GetSystemMetrics(76), _u32.GetSystemMetrics(77)
    return x, y, x + _u32.GetSystemMetrics(78), y + _u32.GetSystemMetrics(79)


def _on_screen(f):
    x0, y0, x1, y1 = _virtual_screen()
    return f[0] < x1 and f[0] + f[2] > x0 and f[1] < y1 and f[1] + f[3] > y0


def _flash(f):
    """点之前在目标四周闪一下框（0.35 秒），让人看得见 Vesper 要点哪里。画不出来就算了。"""
    if not f:
        return
    x, y, w, h = f[0] - 3, f[1] - 3, f[2] + 6, f[3] + 6
    t = 3
    ex = 0x00000008 | 0x00000080 | 0x08000000 | 0x00000020 | 0x00080000  # TOPMOST|TOOLWINDOW|NOACTIVATE|TRANSPARENT|LAYERED
    wins = []
    try:
        for (a, b, c, d) in ((x, y, w, t), (x, y + h - t, w, t), (x, y, t, h), (x + w - t, y, t, h)):
            hw = _u32.CreateWindowExW(ex, "Static", None, 0x80000000 | 0x4, a, b, c, d, None, None, None, None)  # POPUP|SS_BLACKRECT
            if hw:
                _u32.SetLayeredWindowAttributes(hw, 0, 230, 0x2)  # LWA_ALPHA
                _u32.ShowWindow(hw, 4)  # SW_SHOWNOACTIVATE
                wins.append(hw)
        msg = wintypes.MSG()
        t0 = time.time()
        while time.time() - t0 < 0.35:
            while _u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE：让框画出来
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.02)
    except OSError:
        pass
    finally:
        for hw in wins:
            _u32.DestroyWindow(hw)


def _front():
    """前台应用：{name, bundle（exe 名，小写）, pid}。"""
    if not IS_WIN:
        return {"name": "", "bundle": "", "pid": 0}
    hwnd = _u32.GetForegroundWindow()
    pid = appctl.real_pid(hwnd) if hwnd else 0
    if not pid:
        return {"name": "", "bundle": "", "pid": 0}
    name, exe = appctl.describe(pid)
    return {"name": name, "bundle": _bundle(hwnd, exe), "pid": pid}


_CRED = re.compile(r"(凭据管理器|凭据管理|Credential Manager|用户帐户|用户账户|User Accounts|BitLocker)", re.I)


def _bundle(hwnd, exe):
    """应用身份。资源管理器里打开的「凭据管理器」「用户帐户」这类控制面板页，单独标出来（hands.py 当敏感应用）。"""
    if exe == "explorer.exe" and hwnd and _CRED.search(appctl.title_of(hwnd) or ""):
        return "explorer.exe:credentials"
    return exe


def _focused_password_hwnd():
    """Win32 层面看：键盘焦点所在的输入框是不是密码框（ES_PASSWORD）。UI 自动化读不到时的第二道保险。"""
    fg = _u32.GetForegroundWindow()
    if not fg:
        return False
    gti = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    tid = _u32.GetWindowThreadProcessId(fg, None)
    if not _u32.GetGUIThreadInfo(tid, ctypes.byref(gti)) or not gti.hwndFocus:
        return False
    buf = ctypes.create_unicode_buffer(64)
    _u32.GetClassNameW(gti.hwndFocus, buf, 64)
    return "edit" in buf.value.lower() and bool(_u32.GetWindowLongW(gti.hwndFocus, -16) & 0x20)  # GWL_STYLE, ES_PASSWORD


# ---------------------------------------------------------------- 读元素

def _rect4(r):
    """UI 自动化的 RECT → [x, y, 宽, 高]；没有框返回 None。"""
    try:
        w, h = r.right - r.left, r.bottom - r.top
    except AttributeError:
        return None
    if w <= 0 or h <= 0:
        return None
    return [int(r.left), int(r.top), int(w), int(h)]


def _cached(el, pid, default=None):
    try:
        v = el.GetCachedPropertyValue(pid)
    except Exception:  # noqa: BLE001
        return default
    return default if v is None else v


def _target(req):
    """要读哪个应用：pid / name（应用名、exe 名）/ 前台。返回 (pid, [窗口]) 或 (None, 错误)。"""
    pid = req.get("pid")
    if isinstance(pid, int) and pid > 0:
        wins = [h for h in appctl.top_windows() if appctl.real_pid(h) == pid]
        return (pid, wins) if wins else (None, "这个程序没有打开的窗口")
    name = str(req.get("name") or "").strip()
    if name:
        hits = appctl.windows_named(name)
        if not hits:
            return None, "「%s」没在运行" % name
        pid = hits[0][1]
    else:
        fg = _u32.GetForegroundWindow()
        if not fg:
            return None, "找不到前台应用（可能在锁屏或系统对话框）"
        pid = appctl.real_pid(fg)
    if not pid:
        return None, "找不到前台应用"
    # 这个应用所有看得见的顶层窗口（含弹出菜单、对话框），前台那个放最前
    fg = _u32.GetForegroundWindow()
    wins = [h for h in appctl.top_windows() if appctl.real_pid(h) == pid]
    wins.sort(key=lambda h: 0 if h == fg else 1)
    return pid, wins[:6]


def _cond_actionable(uia):
    c = uia.CreatePropertyCondition(P_HAS_INVOKE, True)
    for t in ACTIONABLE:
        c = uia.CreateOrCondition(c, uia.CreatePropertyCondition(P_TYPE, t))
    return uia.CreateOrCondition(c, uia.CreatePropertyCondition(P_TYPE, CT_ID["Document"]))


def _elements(req):
    if not IS_WIN:
        return {"ok": False, "error": NOT_WIN}
    pid, wins = _target(req)
    if pid is None:
        return {"ok": False, "error": wins}
    mx = max(10, min(400, int(req.get("max") or 250)))
    uia = _uia()
    cr = uia.CreateCacheRequest()
    for p in CACHE_PROPS:
        cr.AddProperty(p)
    cond = _cond_actionable(uia)
    menu_cond = uia.CreatePropertyCondition(P_TYPE, CT_ID["MenuBar"])
    item_cond = uia.CreatePropertyCondition(P_TYPE, CT_ID["MenuItem"])
    out, refs, frames, seen = [], [], [], set()
    truncated = False
    deadline = time.time() + 3.0

    def take(el, in_menu):
        nonlocal truncated
        if len(out) >= mx:
            truncated = True
            return
        rid = _cached(el, P_RUNTIME)
        rid = tuple(rid) if isinstance(rid, (tuple, list)) else None
        if rid in seen and rid is not None:
            return
        ct = int(_cached(el, P_TYPE, 0) or 0)
        try:
            f = _rect4(el.CachedBoundingRectangle)
        except Exception:  # noqa: BLE001
            f = None
        visible = f is not None and f[2] > 1 and f[3] > 1 and _on_screen(f) and not _cached(el, P_OFFSCREEN, False)
        if not visible and not in_menu:
            return
        secure = bool(_cached(el, P_PASSWORD, False))
        text = ct in TEXT_TYPES
        if ct == CT_ID["Document"]:  # 网页、只读文档不算输入框；能编辑的文档（Win11 记事本、写字板）才算
            has_value = bool(_cached(el, P_HAS_VALUE, False))
            editable = (has_value and not _cached(el, P_READONLY, True)) or (not has_value and bool(_cached(el, P_HAS_TEXT, False)))
            text = editable and bool(_cached(el, P_FOCUSABLE, False))
            if not text and not _cached(el, P_HAS_INVOKE, False):
                return
        label = str(_cached(el, P_NAME, "") or "").strip()
        if not label and not secure and not text:
            label = str(_cached(el, P_VALUE, "") or "").strip()  # 密码框和输入框的内容绝不读
        hlp = str(_cached(el, P_HELP, "") or "").strip()
        if not label:
            label = hlp
        if not label and not text:
            return
        d = {"i": len(out), "role": CT.get(ct, "Control"), "label": _clip(label, 80),
             "enabled": bool(_cached(el, P_ENABLED, True))}
        kind = str(_cached(el, P_LTYPE, "") or "").strip()
        if kind:
            d["kind"] = _clip(kind, 20)
        if hlp and hlp != label:
            d["help"] = _clip(hlp, 60)
        if text:
            d["text"] = True
        if secure:
            d["secure"] = True
        if in_menu:
            d["menu"] = True
        if f:
            d["frame"] = f
        if rid is not None:
            seen.add(rid)
        out.append(d)
        refs.append(el)
        frames.append(f)

    def each(arr):
        for k in range(arr.Length if arr else 0):
            yield arr.GetElement(k)

    roots = []
    for h in wins:
        try:
            roots.append(uia.ElementFromHandle(h))
        except Exception:  # noqa: BLE001  窗口刚关掉
            continue
    menus = []
    for root in roots:
        if time.time() > deadline:
            truncated = True
            break
        try:
            for el in each(root.FindAllBuildCache(SCOPE_DESC, cond, cr)):
                take(el, False)
            menus += list(each(root.FindAllBuildCache(SCOPE_DESC, menu_cond, cr)))
        except Exception:  # noqa: BLE001  某个窗口读不出来，接着读别的
            continue
    # 菜单栏只要第一层（「文件」「编辑」…），排在最后，和 Mac 版一样
    for mb in menus:
        try:
            items = list(each(mb.FindAllBuildCache(SCOPE_CHILDREN, item_cond, cr)))
        except Exception:  # noqa: BLE001
            continue
        for el in items:
            rid = _cached(el, P_RUNTIME)
            rid = tuple(rid) if isinstance(rid, (tuple, list)) else None
            if rid is not None and rid in seen:  # 前面当普通按钮收过了：改标成菜单
                for k, d in enumerate(out):
                    if refs[k] is not None and tuple(_cached(refs[k], P_RUNTIME) or ()) == rid:
                        d["menu"] = True
                continue
            take(el, True)
    sid = uuid.uuid4().hex[:8]
    with _lock:
        _snaps[sid] = {"id": sid, "pid": pid, "hwnd": wins[0] if wins else None, "at": time.time(),
                       "elems": refs, "frames": frames}
        _snap_order.append(sid)
        while len(_snap_order) > 4:
            _snaps.pop(_snap_order.pop(0), None)
    name, exe = appctl.describe(pid)
    return {"ok": True, "snap": sid, "app": name, "bundle": _bundle(wins[0] if wins else None, exe),
            "elements": out, "truncated": truncated}


# ---------------------------------------------------------------- 动作

def _lookup(req):
    sid, i = req.get("snap"), req.get("i")
    with _lock:
        snap = _snaps.get(sid) if isinstance(sid, str) else None
    if not snap or not isinstance(i, int) or i < 0 or i >= len(snap["elems"]):
        return None
    if time.time() - snap["at"] > SNAP_TTL:
        return None
    return snap, i, snap["elems"][i]


def _still_there(snap, i, el):
    """元素是否还在原处（页面变了就不点，免得点错）。"""
    try:
        now = _rect4(el.CurrentBoundingRectangle)
        el.CurrentControlType  # noqa: B018  元素没了这里会报错
    except Exception:  # noqa: BLE001
        return False
    old = snap["frames"][i]
    if old is None:
        return True
    if now is None:
        return False
    return abs(now[0] - old[0]) < 8 and abs(now[1] - old[1]) < 8


def _pattern(el, pid, iface):
    try:
        p = el.GetCurrentPattern(pid)
        return p.QueryInterface(iface) if p else None
    except Exception:  # noqa: BLE001  不支持这个模式
        return None


def _is_password(el):
    try:
        return bool(el.CurrentIsPassword)
    except Exception:  # noqa: BLE001
        return False


def _focus_or_click(snap, i, el):
    try:
        el.SetFocus()
        return "focus"
    except Exception:  # noqa: BLE001
        f = snap["frames"][i]
        if not f:
            raise
        appctl.activate(snap["hwnd"])
        _click_at(f[0] + f[2] / 2, f[1] + f[3] / 2)
        return "click"


def _press(req):
    why = _guard()
    if why:
        return {"ok": False, "error": why}
    lk = _lookup(req)
    if not lk:
        return {"ok": False, "error": "这批屏幕元素已经过期，先重新看一下屏幕"}
    snap, i, el = lk
    if not _still_there(snap, i, el):
        return {"ok": False, "error": "屏幕变了，这个元素不在原来的位置，先重新看一下"}
    try:
        if not el.CurrentIsEnabled:
            return {"ok": False, "error": "这个按钮现在是灰的，点不了"}
    except Exception:  # noqa: BLE001
        pass
    why = _blocked(snap["pid"])
    if why:
        return {"ok": False, "error": why}
    _flash(snap["frames"][i])
    if is_stopped():
        return {"ok": False, "error": "刚被急停了"}
    mod = _W["mod"]
    try:
        ct = int(el.CurrentControlType)
    except Exception:  # noqa: BLE001
        ct = 0
    method = None
    if ct in TEXT_TYPES:
        method = _focus_or_click(snap, i, el)
    else:
        for pid, iface, call, name in ((PAT_INVOKE, mod.IUIAutomationInvokePattern, "Invoke", "invoke"),
                                       (PAT_TOGGLE, mod.IUIAutomationTogglePattern, "Toggle", "toggle"),
                                       (PAT_SELITEM, mod.IUIAutomationSelectionItemPattern, "Select", "select"),
                                       (PAT_EXPAND, mod.IUIAutomationExpandCollapsePattern, "Expand", "expand"),
                                       (PAT_LEGACY, mod.IUIAutomationLegacyIAccessiblePattern, "DoDefaultAction", "default")):
            p = _pattern(el, pid, iface)
            if p is None:
                continue
            try:
                _bg(getattr(p, call))
                method = name
                break
            except Exception:  # noqa: BLE001  这个模式点不了，换下一个
                continue
        if not method:
            f = snap["frames"][i]
            if not f:
                return {"ok": False, "error": "点不了这个元素"}
            appctl.activate(snap["hwnd"])
            _click_at(f[0] + f[2] / 2, f[1] + f[3] / 2)
            method = "click"
    try:
        lab = _clip(el.CurrentName, 40)
    except Exception:  # noqa: BLE001
        lab = "?"
    _log("press app=%s role=%s label=%s via=%s" % (appctl.describe(snap["pid"])[1], CT.get(ct, ct), lab, method))
    return {"ok": True, "method": method}


def _type(req):
    why = _guard()
    if why:
        return {"ok": False, "error": why}
    text = req.get("text")
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": "没有要输入的内容"}
    if len(text) > MAX_TEXT:
        return {"ok": False, "error": "一次最多输入 %d 个字" % MAX_TEXT}
    uia = _uia()
    if req.get("snap") is not None:
        lk = _lookup(req)
        if not lk:
            return {"ok": False, "error": "这批屏幕元素已经过期，先重新看一下屏幕"}
        snap, i, el = lk
        if not _still_there(snap, i, el):
            return {"ok": False, "error": "屏幕变了，先重新看一下"}
        if _is_password(el):
            return {"ok": False, "error": "这是密码框，Vesper 不往密码框里输入"}
        why = _blocked(snap["pid"])
        if why:
            return {"ok": False, "error": why}
        _flash(snap["frames"][i])
        appctl.activate(snap["hwnd"])
        _focus_or_click(snap, i, el)
        time.sleep(0.12)
    fg = _u32.GetForegroundWindow()
    why = _blocked(appctl.real_pid(fg) if fg else 0)
    if why:
        return {"ok": False, "error": why}
    try:
        foc = uia.GetFocusedElement()
    except Exception:  # noqa: BLE001
        foc = None
    if (foc is not None and _is_password(foc)) or _focused_password_hwnd():
        return {"ok": False, "error": "现在焦点在密码框里，Vesper 不往密码框里输入"}
    units = text.encode("utf-16-le")
    codes = [int.from_bytes(units[k:k + 2], "little") for k in range(0, len(units), 2)]
    k, typed = 0, 0
    while k < len(codes):
        if is_stopped():
            return {"ok": False, "error": "输入到一半被急停了", "typed": typed}
        c = codes[k]
        if c == 0x0D:  # \r\n 当一个回车
            k += 1
            if k < len(codes) and codes[k] == 0x0A:
                continue
            c = 0x0A
        if c == 0x0A or c == 0x09:  # 换行、Tab 发真的按键，很多程序不认 Unicode 的换行字符
            vk = 0x0D if c == 0x0A else 0x09
            _send([_ki(vk), _ki(vk, flags=0x2)])
            n = 1
        else:
            n = 2 if 0xD800 <= c <= 0xDBFF and k + 1 < len(codes) else 1  # 代理对（emoji 等）一起发，不切成两半
            ev = []
            for u in codes[k:k + n]:
                ev += [_ki(0, u, 0x4), _ki(0, u, 0x4 | 0x2)]  # KEYEVENTF_UNICODE (| KEYUP)
            _send(ev)
        k += n
        typed += n
        time.sleep(0.008)
    _log("type app=%s chars=%d" % (_front()["bundle"], len(text)))
    return {"ok": True, "chars": len(text)}


VK = {"return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
      "delete": 0x2E, "del": 0x2E, "forwarddelete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21,
      "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "capslock": 0x14, "printscreen": 0x2C,
      "menu": 0x5D, "apps": 0x5D, ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB,
      "\\": 0xDC, "]": 0xDD, "'": 0xDE}
VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({"f%d" % n: 0x6F + n for n in range(1, 25)})
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D}
MODS = {"ctrl": 0x11, "control": 0x11, "cmd": 0x11, "command": 0x11, "opt": 0x12, "alt": 0x12, "option": 0x12,
        "shift": 0x10, "win": 0x5B, "windows": 0x5B, "super": 0x5B, "meta": 0x5B}


def parse_keys(combo):
    """"ctrl+shift+t" → ([修饰键码], 主键码) 或 (None, 错误)。cmd 按 ctrl 算（Mac 的 ⌘ 在 Windows 上就是 Ctrl）。"""
    combo = str(combo or "").lower().replace(" ", "")
    if not combo:
        return None, "没有按键"
    mods, code = [], None
    parts = combo.split("+")
    if combo.endswith("++"):  # ctrl++ 里最后那个 + 是主键
        parts = parts[:-2] + ["="]
    for part in [p for p in parts if p]:
        if part in MODS:
            if MODS[part] not in mods:
                mods.append(MODS[part])
            continue
        if code is not None or part not in VK:
            return None, "不认识的按键：%s" % part
        code = VK[part]
    if code is None:
        return None, "只有修饰键，没有主键"
    if set(mods) >= {0x11, 0x12} and code in (0x2E,):
        return None, "Ctrl+Alt+Del 是系统保留的，程序按不了"
    return (mods, code), None


def _key(req):
    why = _guard()
    if why:
        return {"ok": False, "error": why}
    parsed, err = parse_keys(req.get("keys"))
    if err:
        return {"ok": False, "error": err}
    mods, code = parsed
    fg = _u32.GetForegroundWindow()
    why = _blocked(appctl.real_pid(fg) if fg else 0)
    if why:
        return {"ok": False, "error": why}

    def ki(vk, up):
        return _ki(vk, 0, (0x1 if vk in EXTENDED else 0) | (0x2 if up else 0))
    ev = [ki(m, False) for m in mods] + [ki(code, False), ki(code, True)] + [ki(m, True) for m in reversed(mods)]
    _send(ev)
    _log("key app=%s keys=%s" % (_front()["bundle"], str(req.get("keys"))[:40]))
    return {"ok": True}


def _scroll(req):
    why = _guard()
    if why:
        return {"ok": False, "error": why}
    d = req.get("dir") if req.get("dir") in ("up", "down", "left", "right") else "down"
    try:
        n = max(1, min(15, int(req.get("amount") or 5)))
    except (TypeError, ValueError):
        n = 5
    fg = _u32.GetForegroundWindow()
    if not fg:
        return {"ok": False, "error": "找不到前台窗口"}
    why = _blocked(appctl.real_pid(fg))
    if why:
        return {"ok": False, "error": why}
    # 滚轮作用在鼠标下面的窗口上：鼠标不在前台窗口上，就先挪到它中间
    pt = wintypes.POINT()
    _u32.GetCursorPos(ctypes.byref(pt))
    under = _u32.WindowFromPoint(pt)
    if not under or _u32.GetAncestor(under, 2) != fg:  # GA_ROOT
        r = appctl.rect_of(fg)
        if r:
            _u32.SetCursorPos((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
            time.sleep(0.03)
    # Mac 版按「行」滚；Windows 一格滚轮（120）默认是 3 行，所以每行 40
    total = 40 * n * (1 if d in ("up", "right") else -1)
    flag = 0x0800 if d in ("up", "down") else 0x01000  # WHEEL / HWHEEL（向右为正）
    while total:
        step = max(-120, min(120, total))
        _send([_mi(flag, step)])
        total -= step
        time.sleep(0.01)
    return {"ok": True}


def _open(req):
    why = _guard()
    if why:
        return {"ok": False, "error": why}
    return appctl.open_app(req.get("name"))


def text_of(snap, i):
    """测试用：读一个普通输入框现在的文字（值模式 / 文本模式）。密码框返回 None。不在 call 的命令里，Vesper 拿不到。"""
    def job():
        lk = _lookup({"snap": snap, "i": i})
        if not lk or _is_password(lk[2]):
            return None
        el, mod = lk[2], _W["mod"]
        p = _pattern(el, PAT_VALUE, mod.IUIAutomationValuePattern)
        if p is not None:
            try:
                return str(p.CurrentValue or "")
            except Exception:  # noqa: BLE001
                pass
        p = _pattern(el, PAT_TEXT, mod.IUIAutomationTextPattern)
        if p is not None:
            try:
                return str(p.DocumentRange.GetText(-1) or "")
            except Exception:  # noqa: BLE001
                pass
        return None
    return _run(job, 10) if IS_WIN else None


# ---------------------------------------------------------------- 入口

def status():
    with _lock:
        hk, err, stopped = list(_S["hotkeys"]), _S["uia_error"], _S["stopped"]
    st = {"ok": True, "trusted": IS_WIN and not err, "enabled": enabled(), "stopped": stopped, "front": _front(),
          "platform": "win", "hotkeys": hk, "hotkey": hk[0] if hk else None}
    if not IS_WIN:
        st["error"] = NOT_WIN
    elif err:
        st["error"] = err
    return st


ACTIONS = {"elements": (_elements, False), "press": (_press, True), "type": (_type, True), "key": (_key, True),
           "scroll": (_scroll, True), "open": (_open, False)}


def call(req, timeout=15.0):
    """和 DeckHands.swift 的 handle(req) 一样：收一个请求（dict），回一个结果（dict）。从不抛异常。"""
    if not isinstance(req, dict):
        return {"ok": False, "error": "请求不对"}
    cmd = req.get("cmd") if isinstance(req.get("cmd"), str) else ""
    start()
    # status / arm / stop 不排队：急停要立刻生效，不能等前一个动作做完
    if cmd == "status":
        return status()
    if cmd == "arm":
        _set_stopped(False)
        return status()
    if cmd == "stop":
        stop_now("deck")
        return status()
    if cmd not in ACTIONS:
        return {"ok": False, "error": "未知命令"}
    if not IS_WIN:
        return {"ok": False, "error": NOT_WIN}
    fn, acting = ACTIONS[cmd]
    if acting:
        _acting(True)
    try:
        return _run(lambda: fn(req), max(2.0, float(timeout or 15.0)))
    finally:
        if acting:
            _acting(False)
