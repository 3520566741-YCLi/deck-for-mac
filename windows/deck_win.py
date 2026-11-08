"""Deck for Windows：原生外壳（11-08）。

和 Mac 版共用同一套网页（manager/dashboard.html）和 Python 后台（manager/server.py），这里只做 Windows 上的壳：
- 后台：同一个进程里起 server.py（只听 127.0.0.1:8777）
- 窗口：pywebview（Windows 上用系统自带的 Edge WebView2），关窗口只是收到托盘，不退出
- 托盘：右下角 Deck 图标（pystray），菜单里打开 / 浏览器打开 / 全屏 / 开机自启 / 退出
- 快捷键：Ctrl+Alt+空格 呼出或收起（Mac 上是 ⌥空格；Windows 的 Alt+空格 是窗口菜单，不能占）
- 显示方式（11-08 第三步）：自动 / 窗口 / 顶部面板。顶部面板 = Mac 刘海模式的 Windows 版：
  一个无边框、透明、置顶的小窗口，从屏幕顶部正中「水帘」一样流下来（网页是同一个 ?notch=1 页面），
  Ctrl+Alt+空格 或鼠标在屏幕顶边正中停一下就弹出，点别处 / Esc 收起。自动 = 接着第二块屏就铺在那块屏上，没接就用顶部面板。
- 只开一个：再双击 Deck 只会把已有窗口叫到前面

用法：
  Deck.exe              正常启动
  Deck.exe --hidden     开机自启时用：只放托盘，不弹窗口
  Deck.exe --selftest   不开窗口，起后台、等系统卡读到数据后打印结果退出（CI 用，0 = 通过）
  Deck.exe --voice      语音服务子进程（Deck 自己拉起并守护，见 voice_launch.py；不用手动跑）
开发时：python windows/deck_win.py（需要 pip install -r windows/requirements-win.txt）
"""
import ctypes
import json
import os
import sys
import threading
import time
import urllib.request

APP = "Deck"
PORT = 8777
HOTKEY_TEXT = "Ctrl+Alt+空格"
IS_WIN = sys.platform == "win32"


# ---------- 路径 ----------

def bundle_dir():
    """manager/ 在哪：打包后在 exe 旁边的 _internal/manager，开发时在仓库里。"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "manager")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manager")


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    d = os.path.join(base, APP, "logs")
    os.makedirs(d, exist_ok=True)
    return d


def redirect_logs():
    """打包成窗口程序后没有控制台（sys.stdout 是 None），把输出写进 %LOCALAPPDATA%\\Deck\\logs\\server.log。"""
    if sys.stdout is not None and sys.stdout.isatty():
        return
    path = os.path.join(log_dir(), "server.log")
    try:  # 超过 5MB 就换新文件，旧的留一份 .1
        if os.path.getsize(path) > 5 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115  进程活多久它开多久
    sys.stdout = sys.stderr = f


# ---------- 只开一个 ----------

_MUTEX = None


def already_running():
    """用一个命名互斥体判断是不是已经开着；开着就把它的窗口叫到前面。"""
    global _MUTEX
    if not IS_WIN:
        return False
    k32 = ctypes.windll.kernel32
    _MUTEX = k32.CreateMutexW(None, False, "Local\\DeckForWindows")
    if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, APP)
    if hwnd:
        u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
    return True


# ---------- 后台 ----------

def start_server():
    """在后台线程里跑 manager/server.py 的 main()。server.py 在导入时就会建好数据源，所以先把 manager 放进 sys.path。"""
    mdir = bundle_dir()
    if mdir not in sys.path:
        sys.path.insert(0, mdir)
    sys.argv = [sys.argv[0], "--no-open"]
    import server  # noqa: PLC0415  要等 sys.path 设好才能导入

    t = threading.Thread(target=server.main, name="deck-server", daemon=True)
    t.start()
    return server


def wait_ready(timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % PORT, timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def _report(obj):
    """打包后的 Deck.exe 是窗口程序，没有控制台：结果同时写进文件（DECK_SELFTEST_OUT，默认日志目录下 selftest.json）。"""
    line = json.dumps(obj, ensure_ascii=False)
    print("SELFTEST", line, flush=True)
    out = os.environ.get("DECK_SELFTEST_OUT") or os.path.join(log_dir(), "selftest.json")
    with open(out, "w", encoding="utf-8") as f:
        f.write(line)


def selftest():
    """CI 用：后台起得来、系统卡能读到 CPU / 内存 / 磁盘，网页能打开且是 Windows 版。"""
    start_server()
    if not wait_ready():
        _report({"ok": False, "error": "后台 40 秒没起来"})
        return 1
    url = "http://127.0.0.1:%d" % PORT
    sysd, deadline = None, time.time() + 30
    while time.time() < deadline:
        with urllib.request.urlopen(url + "/api/dash", timeout=5) as r:
            snap = json.loads(r.read())
        st = snap["sources"].get("system") or {}
        d = st.get("data") or {}
        if st.get("status") == "ok" and d.get("cpu") is not None:
            sysd = d
            break
        time.sleep(1)
    if not sysd:
        _report({"ok": False, "error": "系统卡 30 秒没读到数据"})
        return 1
    with urllib.request.urlopen(url + "/dash", timeout=5) as r:
        html = r.read().decode("utf-8")
    checks = {
        "cpu": sysd.get("cpu") is not None,
        "memory": bool((sysd.get("memory") or {}).get("total_gb")),
        "disk": bool((sysd.get("disk") or {}).get("total_gb")),
        "model": bool((sysd.get("model") or {}).get("chip")),
        "page_is_windows": 'class="skin-soft os-win"' in html and '"os": "win"' in html,
    }
    ok = all(checks.values())
    _report({"ok": ok, "checks": checks, "system": {k: sysd.get(k) for k in ("cpu", "memory", "gpu", "disk", "battery", "model")}})
    return 0 if ok else 1


# ---------- 开机自启（当前用户，不要管理员权限）----------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_cmd():
    if getattr(sys, "frozen", False):
        return '"%s" --hidden' % sys.executable
    return '"%s" "%s" --hidden' % (sys.executable.replace("python.exe", "pythonw.exe"), os.path.abspath(__file__))


def autostart_get():
    if not IS_WIN:
        return False
    import winreg  # noqa: PLC0415
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, APP)[0])
    except OSError:
        return False


def autostart_set(on):
    if not IS_WIN:
        return
    import winreg  # noqa: PLC0415
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, _launch_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# ---------- 全局快捷键 ----------

def hotkey_loop(on_press):
    """Ctrl+Alt+空格。RegisterHotKey 要在自己的线程里收消息；被别的软件占了就算了（托盘照样能开）。"""
    if not IS_WIN:
        return
    from ctypes import wintypes  # noqa: PLC0415
    u32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_SPACE, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x20, 0x0312
    if not u32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_SPACE):
        print("[deck-win] %s 被别的软件占了，快捷键不可用" % HOTKEY_TEXT, flush=True)
        return
    msg = wintypes.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            try:
                on_press()
            except Exception as e:  # noqa: BLE001  快捷键出错不能把线程弄死
                print("[deck-win] hotkey:", e, flush=True)


# ---------- 显示方式（自动 / 窗口 / 顶部面板）----------

MODES = ("auto", "ext", "notch")
def _zh():
    """托盘和窗口标题用哪种语言：跟 Windows 的显示语言（中文系统用中文，其余英文）。网页自己在设置里另选。"""
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x04
    except Exception:  # noqa: BLE001
        return True


ZH = _zh() if sys.platform == "win32" else True


def T(zh, en):
    return zh if ZH else en


MODE_NAMES = {"auto": T("自动", "Auto"), "ext": T("窗口", "Window"), "notch": T("顶部面板", "Top panel")}
PANEL_TITLE = "Deck 顶部面板"
PANEL_SIDE, PANEL_BOTTOM = 40, 64      # 面板四周留给阴影的透明边（逻辑像素；阴影是 0 22px 34px）
PANEL_NW, PANEL_NH = 120, 36           # 收起时的「小胶囊」大小（Windows 没有刘海，从顶边正中这一小块流下来）
PANEL_SIZES = {"s": (620, 300), "m": (720, 400), "l": (840, 480)}   # 和 dashboard.html 刘海模式的 SIZES 一致


def _mode_file():
    return os.path.join(os.path.dirname(log_dir()), "display.json")


def mode_get():
    try:
        with open(_mode_file(), encoding="utf-8") as f:
            m = json.load(f).get("mode")
        return m if m in MODES else "ext"
    except (OSError, ValueError, AttributeError):
        return "ext"   # 第一次装好：普通窗口，最不意外


def mode_set(m):
    if m not in MODES:
        return
    tmp = _mode_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mode": m}, f)
    os.replace(tmp, _mode_file())


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


class Win32:
    """顶部面板要的几个 Win32 调用（物理像素；进程已经是 Per-Monitor DPI aware）。64 位下句柄要写清 argtypes。"""

    def __init__(self):
        from ctypes import wintypes as W  # noqa: PLC0415
        self.u = u = ctypes.windll.user32
        u.GetForegroundWindow.restype = W.HWND
        u.MonitorFromPoint.argtypes = [_POINT, W.DWORD]
        u.MonitorFromPoint.restype = W.HANDLE
        u.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        u.MonitorFromWindow.restype = W.HANDLE
        u.GetMonitorInfoW.argtypes = [W.HANDLE, ctypes.POINTER(_MONITORINFO)]
        u.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(_RECT)]
        u.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
        u.SetForegroundWindow.argtypes = [W.HWND]
        u.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        u.GetWindowThreadProcessId.restype = W.DWORD
        u.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
        u.FindWindowW.restype = W.HWND
        u.GetWindowLongW.argtypes = [W.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [W.HWND, ctypes.c_int, ctypes.c_long]
        u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.k = ctypes.windll.kernel32
        try:
            self.sh = ctypes.windll.shcore
            self.sh.GetDpiForMonitor.argtypes = [W.HANDLE, ctypes.c_int, ctypes.POINTER(W.UINT), ctypes.POINTER(W.UINT)]
        except (AttributeError, OSError):
            self.sh = None

    def monitors(self):
        return max(1, self.u.GetSystemMetrics(80))   # SM_CMONITORS

    def cursor(self):
        p = _POINT()
        self.u.GetCursorPos(ctypes.byref(p))
        return p

    def monitor_at(self, p=None):
        """(显示器矩形, 工作区矩形, 缩放)；p 不给就是鼠标所在那块屏。"""
        h = self.u.MonitorFromPoint(p or self.cursor(), 2)   # MONITOR_DEFAULTTONEAREST
        return self._info(h)

    def _info(self, h):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        self.u.GetMonitorInfoW(h, ctypes.byref(mi))
        scale = 1.0
        if self.sh:
            from ctypes import wintypes as W  # noqa: PLC0415
            dx, dy = W.UINT(96), W.UINT(96)
            if self.sh.GetDpiForMonitor(h, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                scale = dx.value / 96.0
        return mi.rcMonitor, mi.rcWork, scale, bool(mi.dwFlags & 1)

    def all_monitors(self):
        out = []
        cb_t = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_void_p)

        def cb(h, _dc, _r, _l):
            out.append(self._info(h))
            return 1
        self.u.EnumDisplayMonitors(None, None, cb_t(cb), None)
        return out

    def rect(self, hwnd):
        r = _RECT()
        self.u.GetWindowRect(hwnd, ctypes.byref(r))
        return r

    def place(self, hwnd, x, y, w, h, top=True):
        # HWND_TOPMOST=-1；SWP_NOACTIVATE=0x10
        self.u.SetWindowPos(hwnd, -1 if top else 0, int(x), int(y), int(w), int(h), 0x10 | (0 if top else 0x4))

    def tool_window(self, hwnd):
        """不进任务栏、不进 Alt+Tab：WS_EX_TOOLWINDOW，去掉 WS_EX_APPWINDOW。"""
        ex = self.u.GetWindowLongW(hwnd, -20)
        self.u.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)

    def focus(self, hwnd):
        """把面板放到前台（拿到键盘，Esc 才收得起来）。系统不让后台程序抢前台时，借前台线程的输入队列一下。"""
        if self.u.GetForegroundWindow() == hwnd:
            return True
        fg = self.u.GetForegroundWindow()
        me = self.k.GetCurrentThreadId()
        other = self.u.GetWindowThreadProcessId(fg, None) if fg else 0
        if other and other != me:
            self.u.AttachThreadInput(me, other, True)
        try:
            self.u.SetForegroundWindow(hwnd)
        finally:
            if other and other != me:
                self.u.AttachThreadInput(me, other, False)
        return self.u.GetForegroundWindow() == hwnd

    def button_down(self):
        return bool(self.u.GetAsyncKeyState(0x01) & 0x8000 or self.u.GetAsyncKeyState(0x02) & 0x8000)

    def fullscreen_app(self):
        """前台是全屏程序（游戏、看视频）：鼠标碰顶边别弹面板。桌面和任务栏不算。"""
        fg = self.u.GetForegroundWindow()
        if not fg:
            return False
        buf = ctypes.create_unicode_buffer(64)
        self.u.GetClassNameW(fg, buf, 64)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        r = self.rect(fg)
        m = self._info(self.u.MonitorFromWindow(fg, 2))[0]
        return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


class _Bridge:
    """给网页的 js_api：网页里 webkit.messageHandlers.deckMode / deckNotch.postMessage(...) 都走到这里（见 dashboard.html 的 Windows 外壳垫片）。
    只放一个公开方法，别的都是下划线开头 —— pywebview 会把 js_api 对象上公开的属性一层层暴露给网页。"""

    def __init__(self, shell, which):
        self._shell = shell
        self._which = which

    def post(self, name, msg):
        try:
            m = json.loads(msg) if isinstance(msg, str) else msg
        except ValueError:
            return False
        if isinstance(m, dict) and name in ("deckMode", "deckNotch") and len(json.dumps(m)) < 2000:
            try:
                self._shell.on_message(self._which, name, m)
            except Exception as e:  # noqa: BLE001  网页发来的东西出错不能弄崩外壳
                print("[deck-win] bridge:", name, e, flush=True)
        return True


# ---------- 窗口 + 托盘 ----------

def _hands(op):
    """托盘里的电脑操控开关 / 急停（后台在同一个进程里，状态和设置页是同一份）。"""
    try:
        from winport import hands_win  # noqa: PLC0415  start_server 之后 manager/ 才在 sys.path 里
    except Exception:  # noqa: BLE001
        return False if op == "get" else "Ctrl+Alt+Esc"
    if op == "get":
        return hands_win.enabled()
    if op == "toggle":
        hands_win.set_enabled(not hands_win.enabled())
    elif op == "stop":
        hands_win.stop_now("tray")
    elif op == "hotkey":
        return hands_win.HOTKEY_TEXT
    return None


class Shell:
    def __init__(self, hidden=False):
        import webview  # noqa: PLC0415  pywebview

        self.webview = webview
        self.visible = not hidden
        self.hidden_start = hidden
        self.quitting = False
        self.base = "http://127.0.0.1:%d/dash" % PORT
        self.url = self.base + "?app=1&shell=win"
        self.w32 = Win32() if IS_WIN else None
        self.mode = mode_get()
        self.mons = self.w32.monitors() if self.w32 else 1
        self.lock = threading.RLock()
        # 顶部面板的状态
        self.panel = None
        self.panel_hwnd = None
        self.panel_ready = False
        self.panel_open = False
        self.panel_by = ""          # key（快捷键 / 托盘 / Vesper 叫的）还是 hover（鼠标碰顶边）
        self.panel_fg = False       # 打开后拿到过前台：之后点别处就收
        self.panel_want = False     # 面板还没加载好时就要打开：加载好再开
        self.panel_size = PANEL_SIZES["l"]
        self.hold = False
        self.hold_until = 0.0
        self.close_seq = 0
        # 新拟态浅色底（#e6e9ef 附近），加载前不闪白
        self.win = webview.create_window(APP, self.url, width=1280, height=800, min_size=(960, 620),
                                         hidden=hidden, background_color="#E4E8EE", text_select=False,
                                         js_api=_Bridge(self, "main"))
        self.win.events.closing += self._on_closing
        self.tray = None

    # ---------- 主窗口 ----------
    def _on_closing(self):
        """点右上角 × 只是收进托盘；托盘里点「退出」才真退出。"""
        if self.quitting:
            return True
        self.hide()
        return False

    def show(self):
        self.visible = True
        self.win.show()
        self.win.restore()

    def hide(self):
        self.visible = False
        self.win.hide()

    def toggle(self):
        self.hide() if self.visible else self.show()

    def quit(self):
        self.quitting = True
        if self.tray:
            self.tray.stop()
        if self.panel:
            try:
                self.panel.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.win.destroy()

    # ---------- 显示方式 ----------
    def active(self):
        """现在实际用哪种：notch（顶部面板）或 ext（窗口）。"""
        if self.mode == "notch" or (self.mode == "auto" and self.mons < 2):
            return "notch"
        return "ext"

    def push_mode(self):
        js = "window.__deckMode && window.__deckMode(%s)" % json.dumps({"mode": self.mode, "active": self.active()})
        for w in (self.win, self.panel if self.panel_ready else None):
            if w is not None:
                try:
                    w.evaluate_js(js)
                except Exception:  # noqa: BLE001  页面还没加载好就算了，它加载完会自己来问
                    pass

    def set_mode(self, m, announce=True):
        if m not in MODES:
            return
        before = self.active()
        self.mode = m
        mode_set(m)
        self.apply_mode(before, announce)
        if self.tray:
            self.tray.update_menu()

    def apply_mode(self, before=None, announce=False):
        """切了显示方式（或者插拔了显示器）后把窗口摆对。"""
        now = self.active()
        if now == "notch":
            self.ensure_panel()
            if before == "ext" and self.visible:
                self.hide()                         # 和 Mac 一样：用刘海（顶部面板）时扩展屏窗口收起
            if announce:
                self.open_panel("key")              # 弹一下，让他知道面板在哪
        else:
            self.close_panel()
            if self.mode == "auto" and self.mons >= 2:
                self.to_second_screen()
            elif before == "notch" and announce:
                self.show()
        self.push_mode()

    def to_second_screen(self):
        """自动模式接着第二块屏：主窗口铺满不是主屏的那块。"""
        if not self.w32:
            return
        others = [m for m in self.w32.all_monitors() if not m[3]]
        if not others:
            return
        r = others[0][1]   # 工作区（不压任务栏）
        hwnd = self._hwnd(self.win, APP)
        self.show()
        if hwnd:
            self.w32.u.ShowWindow(hwnd, 9)   # 先还原，才能挪屏
            self.w32.place(hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top, top=False)
            self.w32.u.ShowWindow(hwnd, 3)   # SW_MAXIMIZE

    @staticmethod
    def _hwnd(w, title):
        try:
            h = int(w.native.Handle.ToInt64())
            if h:
                return h
        except Exception:  # noqa: BLE001
            pass
        try:
            return ctypes.windll.user32.FindWindowW(None, title) or None
        except (AttributeError, OSError):
            return None

    # ---------- 顶部面板 ----------
    def ensure_panel(self):
        with self.lock:
            if self.panel is not None:
                return
            url = self.base + "?app=1&notch=1&shell=win&nw=%d&nh=%d" % (PANEL_NW, PANEL_NH)
            w, h = self.panel_size
            # 先放在屏幕外：透明窗口每次导航都会被 pywebview 显示一下，放外面就看不见；加载完再收起
            self.panel = self.webview.create_window(
                PANEL_TITLE, url, width=w + 2 * PANEL_SIDE, height=h + PANEL_BOTTOM, x=-30000, y=-30000,
                frameless=True, easy_drag=False, shadow=False, on_top=True, transparent=True, resizable=False,
                hidden=True, background_color="#000000", text_select=False, js_api=_Bridge(self, "panel"))
            self.panel.events.loaded += self._panel_loaded

    def _panel_loaded(self):
        with self.lock:
            self._panel_hwnd()
            if not self.panel_open:
                try:
                    self.panel.hide()
                except Exception:  # noqa: BLE001
                    pass

    def _panel_hwnd(self):
        if not self.panel_hwnd:
            self.panel_hwnd = self._hwnd(self.panel, PANEL_TITLE)
            if self.panel_hwnd and self.w32:
                self.w32.tool_window(self.panel_hwnd)
        return self.panel_hwnd

    def _place_panel(self):
        if not (self.w32 and self._panel_hwnd()):
            return
        mon, _work, sc, _p = self.w32.monitor_at()
        w, h = self.panel_size
        pw, ph = (w + 2 * PANEL_SIDE) * sc, (h + PANEL_BOTTOM) * sc
        self.w32.place(self.panel_hwnd, mon.left + (mon.right - mon.left - pw) / 2, mon.top, pw, ph)

    def open_panel(self, by="key"):
        with self.lock:
            self.ensure_panel()
            if not self.panel_ready:
                self.panel_want = by
                return
            self.close_seq += 1
            if not self.panel_open:
                self.panel_by, self.panel_fg = by, False
            elif by == "key":
                self.panel_by = "key"
            self.panel_open = True
            self._place_panel()
            self.panel.show()
            self._place_panel()        # show() 可能把它挪回原来的位置，再摆一次
            if self.panel_hwnd and self.w32:
                self.panel_fg = self.w32.focus(self.panel_hwnd) and by == "key"
        try:
            self.panel.evaluate_js("window.__notch && window.__notch.set(true)")
        except Exception:  # noqa: BLE001
            pass

    def close_panel(self):
        with self.lock:
            if not self.panel_open:
                return
            self.panel_open = False
            self.close_seq += 1
            seq = self.close_seq
        try:
            self.panel.evaluate_js("window.__notch && window.__notch.set(false)")
        except Exception:  # noqa: BLE001
            pass

        def later():   # 等「水帘」收完再把窗口藏起来（中途又被叫开就不藏）
            time.sleep(0.6)
            with self.lock:
                if seq == self.close_seq and not self.panel_open:
                    try:
                        self.panel.hide()
                    except Exception:  # noqa: BLE001
                        pass
        threading.Thread(target=later, daemon=True).start()

    def toggle_panel(self):
        self.close_panel() if self.panel_open else self.open_panel("key")

    def on_hotkey(self):
        if self.active() == "notch":
            self.toggle_panel()
        else:
            self.toggle()

    # ---------- 网页发来的消息（和 Mac 原生壳同一套）----------
    def on_message(self, which, name, m):
        t = m.get("t")
        if name == "deckMode":
            if t == "get":
                self.push_mode()
            elif t == "set" and m.get("v") in MODES:
                self.set_mode(m["v"])
            return
        if which != "panel":
            return
        if t in ("ready", "size"):
            try:
                w, h = int(m.get("w") or 0), int(m.get("h") or 0)
            except (TypeError, ValueError):
                w = h = 0
            if 300 <= w <= 1400 and 200 <= h <= 900:
                self.panel_size = (w, h)
                if self.panel_open:
                    self._place_panel()
            if t == "ready":
                self.panel_ready = True
                if not self.panel_open and not self.panel_want:   # 加载时被 pywebview 显示出来的透明窗口：不用等 loaded 事件，先藏好
                    try:
                        self.panel.hide()
                    except Exception:  # noqa: BLE001
                        pass
                self.push_mode()
                want, self.panel_want = self.panel_want, False
                if want:
                    self.open_panel(want)
                elif self.panel_open:      # 面板页刷新了（比如换了语言）：接着开着
                    self.panel.evaluate_js("window.__notch && window.__notch.set(true)")
        elif t == "open":
            self.open_panel("key")         # Vesper 被叫醒了
        elif t == "close":
            self.close_panel()
        elif t == "hold":
            on = bool(m.get("on"))
            if self.hold and not on:
                self.hold_until = time.time() + 4   # 用完了再等 4 秒才收（和 Mac 一样）
            self.hold = on

    # ---------- 盯鼠标 / 前台 / 显示器 ----------
    def watch_loop(self):
        """碰顶边弹出、点别处 / 鼠标离开收起、插拔显示器时自动模式换位置。"""
        if not self.w32:
            return
        w32, dwell, away, last_mons = self.w32, 0.0, 0.0, time.time()
        while not self.quitting:
            step = 0.08 if self.active() == "notch" else 0.5
            time.sleep(step)
            try:
                if time.time() - last_mons > 2:          # 插拔显示器
                    last_mons = time.time()
                    n = w32.monitors()
                    if n != self.mons:
                        before = self.active()
                        self.mons = n
                        if self.mode == "auto":
                            self.apply_mode(before, False)
                            if before == "ext" and self.active() == "notch" and self.visible:
                                self.hide()
                if self.active() != "notch" or not self.panel_ready:
                    dwell = away = 0.0
                    continue
                p = w32.cursor()
                if not self.panel_open:
                    away = 0.0
                    mon, _w, sc, _pr = w32.monitor_at(p)
                    cx = (mon.left + mon.right) / 2
                    at_top = p.y <= mon.top + 1 and abs(p.x - cx) <= (PANEL_NW / 2 + 60) * sc
                    if at_top and not w32.button_down() and not w32.fullscreen_app():
                        dwell += step
                        if dwell >= 0.25:
                            dwell = 0.0
                            self.open_panel("hover")
                    else:
                        dwell = 0.0
                    continue
                dwell = 0.0
                busy = self.hold or time.time() < self.hold_until
                fg = w32.u.GetForegroundWindow() == self.panel_hwnd
                r = w32.rect(self.panel_hwnd)
                if self.panel_by == "hover":
                    # 鼠标碰出来的：在面板里点过就当「正在用」，之后和快捷键打开的一样；没点过，鼠标离开就收
                    pad = 24 * w32.monitor_at(p)[2]
                    inside = r.left - pad <= p.x <= r.right + pad and r.top <= p.y <= r.bottom + pad
                    if inside and w32.button_down():
                        self.panel_by, self.panel_fg = "key", fg
                        away = 0.0
                        continue
                    away = 0.0 if inside or busy else away + step
                    if away >= 0.35:
                        away = 0.0
                        self.close_panel()
                    continue
                away = 0.0
                if fg:
                    self.panel_fg = True
                elif self.panel_fg and not busy:          # 拿到过前台，又点到别的地方了
                    self.close_panel()
            except Exception as e:  # noqa: BLE001  盯的线程不能死
                print("[deck-win] watch:", e, flush=True)
                time.sleep(1)

    # ---------- 托盘 ----------
    def _tray(self):
        import pystray  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        icon_path = os.path.join(bundle_dir(), "static", "dash-icon.png")
        image = Image.open(icon_path) if os.path.exists(icon_path) else Image.new("RGB", (64, 64), "#5B7CFA")
        item = pystray.MenuItem

        def mode_item(k):
            return item(MODE_NAMES[k], lambda i: self.set_mode(k), checked=lambda i: self.mode == k, radio=True)
        menu = pystray.Menu(
            item(T("打开 Deck", "Open Deck"), lambda: self.show(), default=True),
            item(T("弹出顶部面板", "Show top panel"), lambda: self.open_panel("key")),
            item(T("在浏览器里打开", "Open in browser"), lambda: __import__("webbrowser").open(self.base)),
            item(T("全屏 / 退出全屏", "Full screen on / off"), lambda: (self.show(), self.win.toggle_fullscreen())),
            pystray.Menu.SEPARATOR,
            item(T("显示方式", "Display mode"), pystray.Menu(*[mode_item(k) for k in MODES])),
            item(T("允许 Vesper 操作电脑", "Let Vesper control the PC"), lambda i: _hands("toggle"), checked=lambda i: _hands("get")),
            item(T("立即停止 Vesper 操作（%s）", "Stop Vesper now (%s)") % _hands("hotkey"), lambda i: _hands("stop")),
            item(T("开机自动启动", "Start with Windows"), lambda i: autostart_set(not autostart_get()), checked=lambda i: autostart_get()),
            item(T("快捷键：Ctrl+Alt+空格", "Shortcut: Ctrl+Alt+Space"), None, enabled=False),
            pystray.Menu.SEPARATOR,
            item(T("退出 Deck", "Quit Deck"), lambda: self.quit()),
        )
        self.tray = pystray.Icon(APP, image, APP, menu)
        self.tray.run_detached()

    def _started(self):
        """窗口都起来以后：按显示方式摆好；用顶部面板时开机不弹大窗口。"""
        if self.active() == "notch":
            self.ensure_panel()
            if not self.hidden_start:
                if self.visible:
                    self.hide()
                self.open_panel("key")
        elif self.mode == "auto" and self.mons >= 2 and not self.hidden_start:
            self.to_second_screen()
        threading.Thread(target=self.watch_loop, name="deck-watch", daemon=True).start()

    def run(self, after=None):
        if after is None:
            self._tray()
            threading.Thread(target=hotkey_loop, args=(self.on_hotkey,), name="deck-hotkey", daemon=True).start()
        self.webview.start(after or self._started, private_mode=False, storage_path=os.path.join(os.path.dirname(log_dir()), "webview"))


def paneltest(shots_dir=None):
    """CI 用：在真 Windows 上把顶部面板开一遍 —— 加载好、贴着屏幕顶边正中、大小对、不进任务栏、收起后藏好、
    鼠标碰顶边能弹出、离开会收。开着时截一张屏（看透明边和阴影对不对）。结果写进 DECK_SELFTEST_OUT。"""
    start_server()
    if not wait_ready():
        _report({"ok": False, "error": "后台 40 秒没起来"})
        return 1
    sh = Shell(hidden=True)
    sh.mode = "notch"          # 只在这次进程里，不写进设置
    res, checks = {}, {}
    u = ctypes.windll.user32

    def wait(cond, sec):
        end = time.time() + sec
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False

    def body():
        try:
            sh.ensure_panel()
            checks["ready"] = wait(lambda: sh.panel_ready, 45)
            if not checks["ready"]:
                return
            threading.Thread(target=sh.watch_loop, daemon=True).start()
            checks["hidden_at_start"] = wait(lambda: not u.IsWindowVisible(sh.panel_hwnd), 3)
            sh.open_panel("key")
            time.sleep(1.8)
            h = sh.panel_hwnd
            r = sh.w32.rect(h)
            mon, _w, sc, _p = sh.w32.monitor_at(_POINT((r.left + r.right) // 2, max(r.top, 0) + 5))
            w, hh = sh.panel_size
            res.update(rect=[r.left, r.top, r.right, r.bottom], monitor=[mon.left, mon.top, mon.right, mon.bottom], scale=sc, size=[w, hh])
            checks["visible"] = bool(u.IsWindowVisible(h))
            checks["at_top"] = r.top == mon.top
            checks["centered"] = abs((r.left + r.right) - (mon.left + mon.right)) <= 4
            checks["size"] = abs((r.right - r.left) - (w + 2 * PANEL_SIDE) * sc) <= 3 and abs((r.bottom - r.top) - (hh + PANEL_BOTTOM) * sc) <= 3
            checks["no_taskbar"] = bool(u.GetWindowLongW(h, -20) & 0x80)
            checks["page_open"] = sh.panel.evaluate_js("window.__notchOpen === true") is True
            if shots_dir:
                try:
                    from PIL import ImageGrab  # noqa: PLC0415
                    os.makedirs(shots_dir, exist_ok=True)
                    ImageGrab.grab(bbox=(mon.left, mon.top, mon.right, min(mon.bottom, r.bottom + 40)), all_screens=True).save(os.path.join(shots_dir, "panel-open.png"))
                except Exception as e:  # noqa: BLE001
                    res["shot_error"] = str(e)
            sh.close_panel()
            checks["hidden_after_close"] = wait(lambda: not u.IsWindowVisible(h), 3)
            # 鼠标碰顶边正中：弹出；挪开：收起（云电脑没有真鼠标时只记录，不算失败）
            cx = (mon.left + mon.right) // 2
            u.SetCursorPos(cx, mon.top)
            res["hover_open"] = wait(lambda: sh.panel_open and u.IsWindowVisible(h), 3)
            u.SetCursorPos(cx, (mon.top + mon.bottom) // 2 + 200)
            res["hover_close"] = wait(lambda: not sh.panel_open, 3)
        except Exception as e:  # noqa: BLE001
            res["error"] = repr(e)
        finally:
            ok = bool(checks) and all(checks.values()) and "error" not in res
            _report({"ok": ok, "checks": checks, **res})
            sh.quitting = True
            try:
                sh.panel and sh.panel.destroy()
            except Exception:  # noqa: BLE001
                pass
            sh.win.destroy()
            os._exit(0 if ok else 1)

    sh.run(after=lambda: threading.Thread(target=body, daemon=True).start())
    return 1


def utf8_console():
    """Windows 的管道 / 重定向默认用本地代码页（英文系统是 cp1252），print 中文会抛 UnicodeEncodeError。"""
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    if "--voice" in sys.argv:  # 语音服务子进程（Deck.exe --voice，由下面的 voice_launch.start() 拉起）：不开窗口，不走「只开一个」
        import voice_launch  # noqa: PLC0415
        sys.exit(voice_launch.run_service())
    utf8_console()
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if IS_WIN:  # 高分屏下文字清楚（不然 125%/150% 缩放时整窗发虚）；顶部面板的坐标也按物理像素算
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    if "--paneltest" in sys.argv:
        i = sys.argv.index("--paneltest")
        sys.exit(paneltest(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))
    redirect_logs()
    if already_running():
        return
    start_server()
    if not wait_ready():
        print("[deck-win] 后台没起来，看 %s" % os.path.join(log_dir(), "server.log"), flush=True)
    try:  # 语音（第三步）：另起 Deck.exe --voice 子进程跑语音服务 + 麦克风，崩了不影响窗口；8778 已有一份就不起
        import voice_launch  # noqa: PLC0415
        voice_launch.start()
    except Exception as e:  # noqa: BLE001
        print("[deck-win] 语音没起来：%s" % e, flush=True)
    Shell(hidden="--hidden" in sys.argv).run()
    os._exit(0)  # 后台线程都是 daemon，窗口关了就整个退出


if __name__ == "__main__":
    main()
