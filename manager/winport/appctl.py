"""应用的打开 / 退出 / 重启 / 在资源管理器里显示（Windows 版，11-08 第四步）。

给两处用：
- Vesper 的 app_control（vesper_tools._app_generic 在 Windows 上转到 vesper_action），
- Vesper 的手 screen_open（winport.hands_win 的 open 命令）。

怎么找应用（只认系统里登记过的应用，绝不运行模型给的路径）：
1. 开始菜单的快捷方式：%APPDATA% 和 %PROGRAMDATA% 下的 Microsoft\\Windows\\Start Menu\\Programs（*.lnk）；
2. 开始菜单里的商店应用：PowerShell 的 Get-StartApps（名字 + AppID），用 explorer shell:AppsFolder\\<AppID> 打开；
3. 注册表 App Paths（chrome.exe、winword.exe 这种「运行」对话框能直接打开的程序）；
4. 几个系统自带的程序（记事本、计算器、画图、资源管理器…）。
退出 = 给它的窗口发「关闭」（WM_CLOSE，和点右上角 × 一样，会提示保存），不强制结束。
这里只用标准库 + ctypes（不需要 comtypes），在 Linux 上（DECK_OS=win 开发）也能导入，找不到任何应用。
"""
import ctypes
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
CACHE_TTL = 600
_lock = threading.Lock()
_cache = {"at": 0, "rows": []}

# 退出不了：系统外壳、Deck 自己（和 procs.PROTECT 一起用）
NO_QUIT = {"explorer", "deck", "deckvoice", "dwm", "applicationframehost", "searchhost", "startmenuexperiencehost",
           "shellexperiencehost", "textinputhost", "lockapp", "systemsettings"}
# 开始菜单里这些快捷方式不是「应用」
_JUNK = re.compile(r"(uninstall|卸载|readme|read me|help|帮助|website|官网|文档|documentation|license|许可|release notes|"
                   r"what'?s new|更新日志|反馈|feedback|manual|手册)", re.I)
# 系统自带、开始菜单里不一定有快捷方式的程序（只认 System32 / Windows 目录里的这个文件）
_SYS = {"notepad": "记事本", "calc": "计算器", "mspaint": "画图", "explorer": "文件资源管理器", "taskmgr": "任务管理器",
        "control": "控制面板", "cmd": "命令提示符", "powershell": "Windows PowerShell", "regedit": "注册表编辑器",
        "snippingtool": "截图工具", "charmap": "字符映射表", "osk": "屏幕键盘", "magnify": "放大镜", "write": "写字板"}

if IS_WIN:
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    for _n, _a, _r in (
        ("EnumWindows", [WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
        ("EnumChildWindows", [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
        ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
        ("IsWindow", [wintypes.HWND], wintypes.BOOL),
        ("IsIconic", [wintypes.HWND], wintypes.BOOL),
        ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
        ("BringWindowToTop", [wintypes.HWND], wintypes.BOOL),
        ("GetForegroundWindow", [], wintypes.HWND),
        ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        ("GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        ("AttachThreadInput", [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
        ("PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
    ):
        _f = getattr(_u32, _n)
        _f.argtypes, _f.restype = _a, _r
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetCurrentThreadId.restype = wintypes.DWORD
    try:
        _dwm = ctypes.WinDLL("dwmapi")
        _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        _dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    except OSError:
        _dwm = None


def _norm(s):
    s = str(s or "").lower().strip()
    for ext in (".exe", ".lnk"):
        if s.endswith(ext):
            s = s[:-len(ext)]
    return re.sub(r"[\s\-_.·]", "", s)


# ---------------------------------------------------------------- 进程和窗口

def exe_of(pid):
    """进程的 exe 完整路径；读不到返回空字符串（不需要管理员，对管理员进程也能读）。"""
    if not IS_WIN or not pid:
        return ""
    h = _k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        return buf.value if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        _k32.CloseHandle(h)


def pid_of(hwnd):
    if not IS_WIN or not hwnd:
        return 0
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def title_of(hwnd):
    if not IS_WIN or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    _u32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def class_of(hwnd):
    if not IS_WIN or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect_of(hwnd):
    """窗口的 (左, 上, 右, 下)；读不到返回 None。"""
    if not IS_WIN or not hwnd:
        return None
    r = wintypes.RECT()
    if not _u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def _cloaked(hwnd):
    """被系统藏起来的窗口（挂起的商店应用、别的虚拟桌面上的窗口）：看着「可见」，其实不在屏幕上。"""
    if not _dwm:
        return False
    v = ctypes.c_int(0)
    return _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0  # DWMWA_CLOAKED


def real_pid(hwnd):
    """窗口真正属于哪个进程。商店应用（UWP）的外框属于 ApplicationFrameHost.exe，里面那个 CoreWindow 才是应用本身。"""
    pid = pid_of(hwnd)
    if not pid or os.path.basename(exe_of(pid)).lower() != "applicationframehost.exe":
        return pid
    hit = []

    def cb(child, _):
        p = pid_of(child)
        if p and p != pid:
            hit.append(p)
            return False
        return True
    _u32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return hit[0] if hit else pid


def top_windows():
    """屏幕上看得见的顶层窗口（按 Z 顺序，最上面的在前）。返回 [hwnd]。"""
    if not IS_WIN:
        return []
    out = []

    def cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd) and not _cloaked(hwnd):
            r = rect_of(hwnd)
            if r and r[2] - r[0] > 4 and r[3] - r[1] > 4:
                out.append(hwnd)
        return True
    _u32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def app_windows():
    """「应用窗口」：可见、没有主人（不是对话框 / 弹出菜单）、有标题。返回 [(hwnd, 真正的 pid)]。"""
    out = []
    for h in top_windows():
        if _u32.GetWindow(h, 4) or not title_of(h).strip():  # GW_OWNER
            continue
        out.append((h, real_pid(h)))
    return out


def describe(pid):
    """给人看的应用名（exe 的「文件说明」，没有就用 exe 名）和 exe 名（小写，相当于 Mac 的 bundle id）。"""
    path = exe_of(pid)
    exe = os.path.basename(path).lower()
    stem = exe[:-4] if exe.endswith(".exe") else exe
    desc = ""
    try:
        from winport import procs as _procs  # noqa: PLC0415  （要 psutil；缺了就只用 exe 名）
        desc = _procs._file_description(path)
    except ImportError:
        pass
    name = desc if desc and len(desc) <= 40 and desc.lower() != stem else (os.path.basename(path)[:-4] or stem)
    return name or stem or "?", exe


def _window_keys(hwnd, pid):
    """一个窗口能被哪些名字叫到：exe 名、文件说明、标题最后一段（「无标题 - 记事本」→ 记事本）。"""
    name, exe = describe(pid)
    keys = {_norm(exe), _norm(name)}
    t = title_of(hwnd)
    if " - " in t:
        keys.add(_norm(t.rsplit(" - ", 1)[1]))
    return {k for k in keys if k}


def windows_named(q):
    """按名字找正在运行的应用的窗口。先比全名，再比唯一的部分匹配。返回 [(hwnd, pid)]（同一个应用可能多个窗口）。"""
    k = _norm(q)
    if not k:
        return []
    wins = [(h, p, _window_keys(h, p)) for h, p in app_windows()]
    exact = [(h, p) for h, p, keys in wins if k in keys]
    if exact:
        return exact
    part = [(h, p) for h, p, keys in wins if any(len(k) >= 2 and (k in x or (len(x) >= 2 and x in k)) for x in keys)]
    return part if len({p for _, p in part}) == 1 else []


def activate(hwnd):
    """把窗口切到最前面。系统限制后台程序抢前台，依次试：直接切、挂到前台线程上切。返回是否成功。"""
    if not IS_WIN or not hwnd or not _u32.IsWindow(hwnd):
        return False
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if _u32.GetForegroundWindow() == hwnd or _u32.SetForegroundWindow(hwnd):
        time.sleep(0.15)
        return True
    fg = _u32.GetForegroundWindow()
    t_fg = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = _k32.GetCurrentThreadId()
    if t_fg and t_fg != me and _u32.AttachThreadInput(me, t_fg, True):
        try:
            _u32.BringWindowToTop(hwnd)
            _u32.SetForegroundWindow(hwnd)
        finally:
            _u32.AttachThreadInput(me, t_fg, False)
    time.sleep(0.15)
    return _u32.GetForegroundWindow() == hwnd


def close_windows(pid):
    """给这个进程所有可见的顶层窗口发「关闭」。返回发了几个。"""
    try:
        from winport import procs as _procs  # noqa: PLC0415
        return _procs._close_windows(int(pid))
    except ImportError:
        n = 0
        for h in top_windows():
            if pid_of(h) == pid:
                _u32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
                n += 1
        return n


# ---------------------------------------------------------------- 已装的应用

def _start_roots():
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    progdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return [os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(progdata, "Microsoft", "Windows", "Start Menu", "Programs")]


def _cstr(data, off, wide):
    if off <= 0 or off >= len(data):
        return ""
    if wide:
        end = off
        while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
            end += 2
        return data[off:end].decode("utf-16-le", "replace")
    end = data.find(b"\0", off)
    raw = data[off:end if end >= 0 else len(data)]
    return raw.decode("mbcs" if IS_WIN else "latin-1", "replace")


def lnk_target(path):
    """读 .lnk 快捷方式指向的本地文件（MS-SHLLINK 格式里的 LinkInfo.LocalBasePath）。读不出返回空字符串。
    商店应用、只有 ID 列表的快捷方式读不出路径，这没关系：打开时交给系统，退出时按窗口名字找。"""
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 16)
    except OSError:
        return ""
    if len(data) < 0x4C or data[:4] != b"\x4c\0\0\0":
        return ""
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 0x4C
    try:
        if flags & 0x1:  # HasLinkTargetIDList
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        if not flags & 0x2:  # HasLinkInfo
            return ""
        li = pos
        _size, hsize, liflags = struct.unpack_from("<III", data, li)
        if not liflags & 0x1:  # VolumeIDAndLocalBasePath
            return ""
        if hsize >= 0x24:
            base = _cstr(data, li + struct.unpack_from("<I", data, li + 28)[0], True)
            suffix = _cstr(data, li + struct.unpack_from("<I", data, li + 32)[0], True)
        else:
            base = _cstr(data, li + struct.unpack_from("<I", data, li + 16)[0], False)
            suffix = _cstr(data, li + struct.unpack_from("<I", data, li + 24)[0], False)
        return base + suffix
    except struct.error:
        return ""


def _start_apps():
    """Get-StartApps：开始菜单里所有应用（含商店应用）的名字和 AppID。慢（1～2 秒），跟着目录一起缓存。"""
    ps = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=20,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]
    return [(str(x.get("Name") or ""), str(x.get("AppID") or "")) for x in data if isinstance(x, dict)]


def _app_paths():
    out = []
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return out
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            k = winreg.OpenKey(hive, key)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as s:
                        val = os.path.expandvars(str(winreg.QueryValue(s, None) or "")).strip().strip('"')
                except OSError:
                    continue
                if val.lower().endswith(".exe") and os.path.isfile(val):
                    out.append((sub, val))
    return out


def catalog(refresh=False):
    """能打开的应用：[{name, kind: lnk/appid/apppath/sys, target, exe, keys}]。10 分钟缓存一次。"""
    if not IS_WIN:
        return []
    with _lock:
        if not refresh and _cache["rows"] and time.time() - _cache["at"] < CACHE_TTL:
            return _cache["rows"]
    rows, seen = [], set()

    def add(name, kind, target, exe=""):
        k = _norm(name)
        if not k or k in seen or _JUNK.search(name):
            return
        seen.add(k)
        keys = {k}
        if exe:
            keys.add(_norm(os.path.basename(exe)))
        rows.append({"name": name, "kind": kind, "target": target, "exe": exe, "keys": sorted(keys)})

    for root in _start_roots():
        for d, _dirs, files in os.walk(root):
            for f in sorted(files):
                if f.lower().endswith(".lnk"):
                    p = os.path.join(d, f)
                    t = lnk_target(p)
                    add(f[:-4], "lnk", p, t if t.lower().endswith(".exe") else "")
    for name, appid in _start_apps():
        if appid and re.fullmatch(r"[\w.\-{}\\: !]{1,300}", appid):
            add(name, "appid", appid)
    for exe_name, path in _app_paths():
        add(exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name, "apppath", path, path)
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    for stem, label in _SYS.items():
        for d in (os.path.join(windir, "System32"), windir):
            p = os.path.join(d, stem + ".exe")
            if os.path.isfile(p):
                if _norm(stem) not in seen:
                    add(stem, "sys", p, p)
                for r in rows:  # 「记事本」也能叫到 notepad
                    if r["exe"] and os.path.basename(r["exe"]).lower() == stem + ".exe" and _norm(label) not in r["keys"]:
                        r["keys"] = sorted(set(r["keys"]) | {_norm(label)})
                break
    with _lock:
        _cache.update(at=time.time(), rows=rows)
    return rows


def find(q):
    """按名字找已装的应用。返回 (应用, 候选名字)：找到唯一一个时候选为空，找不到时应用为 None。"""
    k = _norm(q)
    if not k:
        return None, []
    rows = catalog()
    rank = {"lnk": 0, "appid": 1, "apppath": 2, "sys": 3}
    exact = sorted([r for r in rows if k in r["keys"]], key=lambda r: rank[r["kind"]])
    if exact:
        return exact[0], []
    part = [r for r in rows if any(k in x or (len(x) >= 2 and x in k) for x in r["keys"])]
    if len(part) == 1:
        return part[0], []
    return None, [r["name"] for r in part[:8]]


def _bad_name(q):
    q = str(q or "").strip()
    return not q or len(q) > 60 or any(c in q for c in "/\\:*?\"<>|\0") or q.startswith("-")


def _running(app, q=None):
    """这个应用正在运行的窗口：[(hwnd, pid)]。有 exe 路径按 exe 名比，商店应用按安装目录比，都没有按名字比。"""
    out = []
    exe = os.path.basename(app.get("exe") or "").lower() if app else ""
    pkg = ""
    if app and app["kind"] == "appid" and "!" in app["target"]:
        fam = app["target"].split("!", 1)[0]  # Microsoft.WindowsNotepad_8wekyb3d8bbwe
        name, _, pub = fam.rpartition("_")
        pkg = (name.lower(), pub.lower())
    for h, p in app_windows():
        path = exe_of(p).lower()
        if exe and os.path.basename(path) == exe:
            out.append((h, p))
        elif pkg and "\\windowsapps\\" in path:
            folder = path.split("\\windowsapps\\", 1)[1].split("\\", 1)[0]
            if folder.startswith(pkg[0] + "_") and folder.endswith(pkg[1]):
                out.append((h, p))
    if not out:
        names = [n for n in ((app or {}).get("name"), q) if n]
        for n in names:
            out = windows_named(n)
            if out:
                break
    return out


def launch(app):
    """启动目录里的一个应用（只启动目录里登记过的东西）。返回是否成功交给了系统。"""
    try:
        if app["kind"] in ("lnk", "apppath", "sys"):
            os.startfile(app["target"])  # noqa: S606  开始菜单快捷方式 / 注册表登记的程序，不是模型给的路径
        elif app["kind"] == "appid":
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["target"]],  # noqa: S603,S607
                             creationflags=CREATE_NO_WINDOW)
        else:
            return False
        return True
    except OSError:
        return False


def open_app(q):
    """打开或切到一个应用（screen_open / app_control open）。返回 {ok, app, how: activated / launched} 或 {error, maybe}。"""
    if not IS_WIN:
        return {"ok": False, "error": "只能在 Windows 上打开应用"}
    if _bad_name(q):
        return {"ok": False, "error": "应用名不对：%s" % str(q or "")[:60]}
    # 先看开着的窗口（快，不用读开始菜单）；没开着再去已装的应用里找
    wins = windows_named(q)
    app, maybe = (None, []) if wins else find(q)
    wins = wins or _running(app, q)
    if wins:
        h, p = wins[0]
        activate(h)
        return {"ok": True, "app": (app or {}).get("name") or describe(p)[0], "how": "activated", "pid": p}
    if not app:
        return {"ok": False, "error": "没找到叫「%s」的应用" % q, "maybe": maybe}
    if not launch(app):
        return {"ok": False, "error": "打不开「%s」" % app["name"]}
    return {"ok": True, "app": app["name"], "how": "launched"}


def _protected(app, wins):
    names = {os.path.basename(exe_of(p)).lower()[:-4] for _, p in wins}
    if app and app.get("exe"):
        names.add(os.path.basename(app["exe"]).lower()[:-4])
    try:
        from winport import procs as _procs  # noqa: PLC0415
        prot = NO_QUIT | _procs.PROTECT
    except ImportError:
        prot = NO_QUIT
    return bool(names & prot) or any(p == os.getpid() for _, p in wins)


def quit_app(app, q=None):
    wins = _running(app, q)
    if not wins:
        return {"ok": True, "note": "「%s」本来就没开" % ((app or {}).get("name") or q)}
    if _protected(app, wins):
        return {"ok": False, "error": "「%s」是系统或 Deck 自己要用的，不能从这里退出" % ((app or {}).get("name") or q)}
    n = sum(close_windows(p) for p in {p for _, p in wins})
    return {"ok": n > 0, "closed": n, "error": None if n else "没找到它的窗口"}


def _wait_gone(app, q, secs=8.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if not _running(app, q):
            return True
        time.sleep(0.4)
    return False


def restart_app(app, q=None):
    if _running(app, q):
        r = quit_app(app, q)
        if not r.get("ok"):
            return r
        if not _wait_gone(app, q):
            return {"ok": False, "error": "「%s」还没退出（可能在问要不要保存），没有重新打开" % app["name"]}
    time.sleep(0.5)
    return {"ok": launch(app)}


def reveal_app(app):
    target = app.get("exe") or (app["target"] if app["kind"] == "lnk" else "")
    if not target or not os.path.exists(target):
        return {"ok": False, "error": "「%s」是商店应用，没有能显示的文件位置" % app["name"]}
    from winport import common  # noqa: PLC0415
    common.reveal(target)
    return {"ok": True}


def vesper_action(q, act, emit, ask):
    """Vesper 的 app_control（没在「我的应用」里登记的应用）。和 Mac 版 vesper_tools._app_generic 一样：
    打开、显示直接做；退出、重启先出确认（ask 就是 Tools._ask）。"""
    if not IS_WIN:
        return {"error": "只能在 Windows 上操作应用"}
    if _bad_name(q):
        return {"error": "应用名不对：%s" % str(q or "")[:60]}
    app, maybe = find(q)
    if not app:
        if act == "quit" and _running(None, q):  # 没装在开始菜单里、但正开着的程序也能退出
            app = {"name": q, "kind": "none", "target": "", "exe": ""}
        else:
            return {"error": "没找到叫「%s」的应用" % q, "maybe": maybe}
    name = app["name"]
    if act == "open":
        r = open_app(q)
        if r.get("ok"):
            emit({"t": "ui", "action": "app_open", "args": {}, "title": name})
            return {"ok": True, "app": name}
        return {"error": r.get("error") or "没打开「%s」" % name}
    if act == "reveal":
        r = reveal_app(app)
        if r.get("ok"):
            emit({"t": "ui", "action": "app_reveal", "args": {}, "title": name})
        return r
    wins = _running(app, q)
    if wins and _protected(app, wins):
        return {"error": "「%s」是系统或 Deck 自己要用的，不能从这里退出" % name}
    if act == "quit":
        if not wins:
            return {"ok": True, "note": "「%s」本来就没开" % name}
        return ask("退出「%s」" % name, lambda: quit_app(app, q), emit)
    if act == "restart":
        if app["kind"] == "none":
            return {"error": "没找到「%s」装在哪，没法重新打开" % name}
        return ask("重启「%s」" % name, lambda: restart_app(app, q), emit)
    return {"error": "未知动作"}
