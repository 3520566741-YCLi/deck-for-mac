"""工具箱（Windows 版，11-08 第二步）：卸载应用（连同残留）、垃圾清理、找大文件、应用更新。

和 Mac 版 apptools.py 的函数名、参数、返回的数据结构一样，server.py 的 /api/tools/、Vesper、工具箱卡片不用改。
Windows 上怎么做：
- 应用列表：读注册表的「卸载」登记（HKLM 64 位 / 32 位 + HKCU），和「设置 → 应用」里看到的基本一致。
  不列系统组件、补丁、没有卸载程序的条目。Microsoft Store 应用暂时不列（它们的卸载走商店，另做）。
- 卸载：运行应用自己的卸载程序（有界面的那个，不偷偷加静默参数），等它跑完；
  确认真的卸掉了，再把勾选的残留（%APPDATA%、%LOCALAPPDATA%、%PROGRAMDATA% 里的同名文件夹、开始菜单快捷方式）移进回收站。
  卸载程序取消了，就一样都不动。
- 垃圾：%TEMP% 里的旧东西、浏览器缓存、缩略图缓存、崩溃转储、错误报告、没下完的下载、旧安装包、旧项目的依赖目录。
- 找大文件：走一遍用户文件夹（不进 AppData，不跟链接）。
- 更新：有 winget 就用 `winget upgrade` 列出能更新的（只读）；点更新时让 winget 打开安装程序的界面。

铁律（和 Mac 版一样）：
- 绝不直接删除。只移进回收站（winport.common.recycle），记下原位置，可以一键放回。
- 先列清单给人看，点确认后才动。页面只能传「清单里第几项」，不能传任意路径；执行前对每个路径再检查一遍。
- 不清空回收站。不碰 Windows 文件夹、Program Files（要管理员的只列出来，让人自己在资源管理器里处理）。
只用标准库 + ctypes + winreg + psutil。在 Linux 上（DECK_OS=win 开发）也能导入，返回空列表或错误。
"""
import ctypes
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid

try:
    import psutil
except ImportError:  # 开发机没装也能导入
    psutil = None

try:
    import deckos
except ImportError:  # 单独测试时
    deckos = None

from winport import common

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import winreg
else:
    winreg = None

HOME = os.path.expanduser("~")
DAY = 86400


def _env(name, *fallback):
    return os.path.normpath(os.environ.get(name) or os.path.join(HOME, *fallback))


APPDATA = _env("APPDATA", "AppData", "Roaming")
LOCALAPPDATA = _env("LOCALAPPDATA", "AppData", "Local")
PROGRAMDATA = os.path.normpath(os.environ.get("PROGRAMDATA") or r"C:\ProgramData")
WINDIR = os.path.normpath(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows")
PROGRAM_FILES = sorted({os.path.normpath(os.environ[k]) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
                        if os.environ.get(k)} | ({r"C:\Program Files", r"C:\Program Files (x86)"} if IS_WIN else set()))
TEMP = os.path.normpath(os.environ.get("TEMP") or os.environ.get("TMP") or os.path.join(LOCALAPPDATA, "Temp"))
START_MENU = [os.path.join(APPDATA, "Microsoft", "Windows", "Start Menu", "Programs"),
              os.path.join(PROGRAMDATA, "Microsoft", "Windows", "Start Menu", "Programs")]
DESKTOPS = [os.path.join(HOME, "Desktop"), os.path.join(os.environ.get("PUBLIC") or r"C:\Users\Public", "Desktop")]


def _support(*parts):
    if deckos:
        return deckos.support(*parts)
    return os.path.join(APPDATA, *parts)


STATE_FILE = _support("Deck", "tools-state.json")
TOOLS_LOG = os.path.join(LOCALAPPDATA, "Deck", "logs", "deck-tools.log")
WINGET = shutil.which("winget") if IS_WIN else None
BREW = None  # Mac 版用来判断有没有 Homebrew；Windows 没有

UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
UNINSTALL_WAIT = 30 * 60  # 卸载程序最多等 30 分钟
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000  # OneDrive「仅在线」的文件，读它会触发下载
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010

# 太常见的名字，单靠名字匹配会误中别的东西
GENERIC_NAMES = {"app", "apps", "code", "data", "helper", "google", "microsoft", "adobe", "apple", "cache", "caches",
                 "logs", "update", "updater", "installer", "launcher", "agent", "service", "client", "desktop", "studio",
                 "player", "editor", "browser", "mail", "music", "notes", "photos", "preview", "terminal", "settings",
                 "windows", "program", "programs", "tools", "common", "shared", "runtime", "driver", "drivers", "setup",
                 "temp", "local", "roaming", "packages", "system", "users", "default", "config", "uninstall"}
# 这些名字的文件夹在 AppData / ProgramData 里永远不当残留（系统或 Deck 自己的）
NEVER_CHILD = {"microsoft", "windows", "packages", "temp", "programs", "deck", "comms", "connecteddevicesplatform",
               "d3dscache", "crashdumps", "packagecache", "publishers", "applicationdata", "history", "virtualstore",
               "microsoftedge", "ssh", "gnupg", "nvidiacorporation", "intel", "amd", "realtek", "regid19910111com.microsoft"}
# 名字对不上的知名应用（按显示名匹配）：卸载时额外看这些位置
FORCE = [
    (r"^Microsoft Visual Studio Code", ["%APPDATA%\\Code", "~\\.vscode"]),
    (r"^Google Chrome$", ["%LOCALAPPDATA%\\Google\\Chrome"]),
    (r"^Slack", ["%APPDATA%\\Slack", "%LOCALAPPDATA%\\slack"]),
    (r"^Cursor", ["%APPDATA%\\Cursor", "~\\.cursor"]),
    (r"^Mozilla Firefox", ["%APPDATA%\\Mozilla\\Firefox", "%LOCALAPPDATA%\\Mozilla\\Firefox"]),
]
# 系统组件和运行库：别的软件要用，不让从这里卸
PROTECT_NAMES = [
    (r"^Microsoft Visual C\+\+ .*(Redistributable|Runtime)", "运行库（别的软件要用）"),
    (r"^Microsoft (\.NET|ASP\.NET|Windows Desktop Runtime)", "运行库（别的软件要用）"),
    (r"^Microsoft Edge( WebView2.*| Update)?$", "系统组件"),
    (r"^Microsoft (Update Health Tools|GameInput)", "系统组件"),
    (r"^Windows (SDK|Software Development Kit|Driver Kit|App Certification Kit|PC Health Check)", "系统组件"),
    (r"^Deck( |$)", "Deck 自己"),
]


# ---------------------------------------------------------------- 小工具
def _home(p):
    return common.tilde(p) if isinstance(p, str) else p


def _norm(s):
    return re.sub(r"[\s\-_.,()'\[\]&+]+", "", (s or "").lower())


def _nc(p):
    return os.path.normcase(os.path.normpath(p))


def _inside(p, root):
    p, root = _nc(p), _nc(root)
    return p == root or p.startswith(root.rstrip("\\/") + os.sep)


def _decode(b):
    if not b:
        return ""
    for enc in ("utf-8", "mbcs" if IS_WIN else "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", "replace")


def _run(cmd, timeout=20, env=None):
    """运行命令，返回 (退出码, 输出, 错误)。Windows 上不弹黑窗口。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
        return r.returncode, _decode(r.stdout), _decode(r.stderr)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return -1, "", str(e)


def _log(lines):
    try:
        os.makedirs(os.path.dirname(TOOLS_LOG), exist_ok=True)
        with open(TOOLS_LOG, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), ln))
    except OSError:
        pass


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


_SLOCK = threading.Lock()


def _save_state(patch):
    with _SLOCK:
        _save_state_locked(patch)


def _save_state_locked(patch):
    st = _load_state()
    st.update(patch)
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = "%s.%d.%s.tmp" % (STATE_FILE, os.getpid(), uuid.uuid4().hex[:6])
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%d %s" if u in ("B", "KB") or n >= 100 else "%.1f %s") % (n, u)
        n /= 1024


def _attrs(st):
    return getattr(st, "st_file_attributes", 0) or 0


def _is_reparse(p):
    """链接、目录联接（junction）、OneDrive 之类的重解析点：一律不跟、不动。"""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(_attrs(st) & FILE_ATTRIBUTE_REPARSE_POINT)


def _tree(p, limit=20000, deadline=None):
    """(总字节, 最近修改时间, 文件数)。不跟链接 / 联接；文件太多或到点就只算到这里。"""
    try:
        st = os.lstat(p)
    except OSError:
        return 0, 0, 0
    if not stat.S_ISDIR(st.st_mode) or _attrs(st) & FILE_ATTRIBUTE_REPARSE_POINT:
        return st.st_size, st.st_mtime, 1
    size, newest, n = 0, 0, 0
    stack = [p]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    s = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _attrs(s) & FILE_ATTRIBUTE_REPARSE_POINT or stat.S_ISLNK(s.st_mode):
                    continue
                if stat.S_ISDIR(s.st_mode):
                    stack.append(e.path)
                    continue
                size += s.st_size
                newest = max(newest, s.st_mtime)
                n += 1
                if n >= limit or (deadline and n % 500 == 0 and time.time() > deadline):
                    return size, newest or st.st_mtime, n
    return size, newest or st.st_mtime, n


def _du(p, timeout=30, strict=False):
    if not os.path.lexists(p) or _is_reparse(p):
        return 0
    return _tree(p, limit=10 ** 7, deadline=time.time() + timeout)[0]


# ---------------------------------------------------------------- 能不能动
def _protected_roots():
    roots = [HOME, APPDATA, LOCALAPPDATA, os.path.join(LOCALAPPDATA, "Programs"), PROGRAMDATA, TEMP, WINDIR] + PROGRAM_FILES
    roots += [os.path.join(HOME, n) for n in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive",
                                               "Favorites", "Contacts", "Links", "Saved Games", "Searches")]
    roots += START_MENU + DESKTOPS
    roots += [os.environ[k] for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "USERPROFILE", "PUBLIC") if os.environ.get(k)]
    return {_nc(r) for r in roots}


# Microsoft 文件夹里只允许动的几处（缓存、错误报告、缩略图、开始菜单里的快捷方式）
ALLOW_MS = [
    os.path.join(APPDATA, "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(PROGRAMDATA, "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data"),
    os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "WER"),
    os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "Explorer"),
    os.path.join(PROGRAMDATA, "Microsoft", "Windows", "WER"),
]
NEVER = [os.path.join(APPDATA, "Microsoft"), os.path.join(LOCALAPPDATA, "Microsoft"), os.path.join(PROGRAMDATA, "Microsoft"),
         os.path.join(LOCALAPPDATA, "Packages"), os.path.join(HOME, ".ssh"), os.path.join(HOME, ".gnupg"),
         os.path.join(HOME, ".aws"), os.path.join(HOME, ".azure"), os.path.join(HOME, ".kube"),
         os.path.join(LOCALAPPDATA, "Deck"), os.path.join(LOCALAPPDATA, "Programs", "Deck"), _support("Deck")]


def _safe_target(p):
    """能不能把 p 移进回收站：返回 None 表示可以，否则返回原因。"""
    if not isinstance(p, str) or not os.path.isabs(p) or "\x00" in p:
        return "路径不对"
    p = os.path.normpath(p)
    if not os.path.lexists(p):
        return "已经不在了"
    if _is_reparse(p):
        return "这是一个链接，不动它"
    parent = os.path.dirname(p)
    if _nc(os.path.realpath(parent)) != _nc(parent):
        return "路径里有链接"
    drive, rest = os.path.splitdrive(p)
    if not rest.strip("\\/") or _nc(p) in _protected_roots():
        return "受保护的位置"
    if _inside(p, WINDIR):
        return "系统位置"
    if "$recycle.bin" in _nc(p).split(os.sep):
        return "受保护的位置"
    if any(_inside(p, r) for r in PROGRAM_FILES):
        return "需要管理员权限，请在资源管理器里自己处理"
    allowed = next((a for a in ALLOW_MS if _inside(p, a) and _nc(p) != _nc(a)), None)
    if not allowed and any(_inside(p, n) for n in NEVER):
        return "受保护的位置"
    if allowed and _nc(allowed).endswith(_nc(os.path.join("Windows", "Explorer"))) \
            and not fnmatch.fnmatch(os.path.basename(p).lower(), "thumbcache_*.db"):
        return "受保护的位置"
    if _nc(parent) == _nc(HOME) and os.path.isdir(p) and not os.path.basename(p).startswith("."):
        return "受保护的位置"
    return None


def to_trash(p):
    """移进回收站（只移动，不删除）。返回回收站里的新路径（找不到位置时是空字符串，资源管理器里仍能还原）。"""
    return common.recycle(p, _safe_target)


def _recycle_many(items, title):
    """items: [{path, bytes}]。返回 {moved, failed, bytes}。"""
    moved, failed, logs = [], [], [title]
    for it in items:
        src = it["path"]
        try:
            dst = to_trash(src)
            moved.append({"src": src, "dst": dst, "bytes": it.get("bytes", 0)})
            logs.append("移到回收站：%s -> %s (%dM)" % (src, dst or "?", round(it.get("bytes", 0) / 1048576)))
        except OSError as e:
            failed.append({"path": _home(src), "why": e.strerror or str(e)})
            logs.append("失败：%s（%s）" % (src, e.strerror or e))
    total = sum(m["bytes"] for m in moved)
    logs.append("完成：移走 %d 项，共 %dM" % (len(moved), round(total / 1048576)))
    _log(logs)
    return {"moved": moved, "failed": failed, "bytes": total}


# ---------------------------------------------------------------- 应用列表（注册表的卸载登记）
def _reg_val(k, name):
    try:
        v, _ = winreg.QueryValueEx(k, name)
        return v
    except OSError:
        return None


def _views():
    """(根键名, 根键, 视图标记)。HKLM 64 位和 32 位（WOW6432Node）各读一遍，HKCU 读一遍。"""
    if not IS_WIN:
        return []
    return [("HKLM", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), ("HKLM32", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
            ("HKCU", winreg.HKEY_CURRENT_USER, 0)]


def _open_root(root, flag):
    return winreg.OpenKey(root, UNINSTALL_KEY, 0, winreg.KEY_READ | flag)


def _clean_path(s):
    s = os.path.expandvars(str(s or "").strip())
    s = re.sub(r",\s*-?\d+$", "", s).strip().strip('"')  # 图标写法「路径,0」
    return os.path.normpath(s) if s else ""


def _split_cmd(cmd):
    """把卸载命令拆成 (程序, 参数)。"""
    cmd = os.path.expandvars(str(cmd or "").strip())
    if not cmd:
        return "", ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
    toks = cmd.split(" ")
    for i in range(len(toks), 0, -1):  # 没加引号、路径里有空格：找最长的、确实存在的 .exe
        cand = " ".join(toks[:i])
        if cand.lower().endswith(".exe") and os.path.isfile(cand):
            return cand, " ".join(toks[i:]).strip()
    return toks[0], " ".join(toks[1:]).strip()


def _app_dir(e):
    """应用装在哪：InstallLocation，没有就看图标或卸载程序所在的文件夹。太宽泛的位置（盘根、Program Files 本身）不算。"""
    cands = [_clean_path(e.get("InstallLocation"))]
    icon = _clean_path(e.get("DisplayIcon"))
    if icon.lower().endswith(".exe"):
        cands.append(os.path.dirname(icon))
    exe = _split_cmd(e.get("UninstallString"))[0]
    if exe and os.path.isabs(exe) and "msiexec" not in exe.lower():
        cands.append(os.path.dirname(exe))
    for d in cands:
        if not d or not os.path.isabs(d):
            continue
        d = os.path.normpath(d)
        drive, rest = os.path.splitdrive(d)
        if not rest.strip("\\/") or _nc(d) in _protected_roots() or _nc(d) in {_nc(x) for x in PROGRAM_FILES} \
                or _inside(d, WINDIR) or _nc(os.path.dirname(d)) == _nc(WINDIR):
            continue
        if _nc(d) == _nc(TEMP) or _inside(d, os.path.join(PROGRAMDATA, "Package Cache")):
            continue
        return d
    return ""


def _read_entries():
    out = []
    for hive, root, flag in _views():
        try:
            k = _open_root(root, flag)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    sub = winreg.OpenKey(k, name)
                except OSError:
                    continue
                with sub:
                    e = {f: _reg_val(sub, f) for f in ("DisplayName", "DisplayVersion", "Publisher", "InstallLocation", "EstimatedSize",
                                                       "UninstallString", "QuietUninstallString", "DisplayIcon", "SystemComponent",
                                                       "ParentKeyName", "ReleaseType", "InstallDate", "WindowsInstaller", "NoRemove")}
                e["hive"], e["key"] = hive, name
                out.append(e)
    return out


def _skip(e):
    name = str(e.get("DisplayName") or "").strip()
    if not name or not e.get("UninstallString"):
        return True
    if e.get("SystemComponent") == 1 or e.get("ParentKeyName"):
        return True
    if str(e.get("ReleaseType") or "") in ("Security Update", "Update Rollup", "Hotfix", "Update", "Service Pack"):
        return True
    return bool(re.match(r"^(Security )?Update for |^Hotfix for |\(KB\d+\)", name))


def _running_exes():
    if not psutil:
        return []
    out = []
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info.get("exe"):
                out.append(_nc(p.info["exe"]))
        except (psutil.Error, OSError):
            continue
    return out


def _running_apps():
    """正在运行的程序路径（一行一个）。Vesper 的 Mac 写法会拿它查「xxx.app/Contents/MacOS/」，在 Windows 上自然查不到。"""
    return "\n".join(_running_exes())


def _plist(app):
    """Mac 应用的 Info.plist；Windows 没有，给空的（Vesper 的老代码会调用）。"""
    return {}


def brew_casks():
    return {}


def _is_running(d, exes):
    if not d:
        return False
    pre = _nc(d).rstrip("\\/") + os.sep
    return any(x.startswith(pre) for x in exes)


def _protect_reason(name, d):
    for pat, why in PROTECT_NAMES:
        if re.search(pat, name, re.I):
            return why
    if d and getattr(sys, "executable", None) and _inside(sys.executable, d):
        return "Deck 自己"
    return ""


def list_apps():
    if not IS_WIN:
        return []
    entries = [e for e in _read_entries() if not _skip(e)]
    exes = _running_exes()
    apps, seen = [], set()
    for e in entries:
        name = str(e["DisplayName"]).strip()
        ver = str(e.get("DisplayVersion") or "").strip()
        dedupe = (name.lower(), ver)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        d = _app_dir(e)
        size = e.get("EstimatedSize")
        installed = None
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", str(e.get("InstallDate") or ""))
        if m:
            try:
                installed = time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0, 0, 0, 0, -1))
            except (OverflowError, ValueError):
                pass
        protected = _protect_reason(name, d) or ("这个应用不允许卸载" if e.get("NoRemove") == 1 else "")
        apps.append({
            "path": "reg:%s:%s" % (e["hive"], e["key"]), "name": name, "bid": e["key"], "version": ver,
            "bytes": int(size) * 1024 if isinstance(size, int) and size > 0 else None, "last_used": None,
            "mas": False, "brew": None, "sparkle": False,
            "running": _is_running(d, exes), "protected": protected,
            # Windows 版多给的
            "publisher": str(e.get("Publisher") or "").strip(), "location": d, "installed": installed,
            "scope": "当前用户" if e["hive"] == "HKCU" else "所有用户", "msi": bool(e.get("WindowsInstaller")),
        })
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def _entry(app_id):
    """按 list_apps 给的 path（reg:根键:子键）重新读一遍注册表登记。没有了返回 None。"""
    m = re.fullmatch(r"reg:(HKLM|HKLM32|HKCU):(.{1,260})", str(app_id or ""))
    if not m or not IS_WIN or "\\" in m.group(2):
        return None
    for hive, root, flag in _views():
        if hive != m.group(1):
            continue
        try:
            with _open_root(root, flag) as k, winreg.OpenKey(k, m.group(2)) as sub:
                e = {f: _reg_val(sub, f) for f in ("DisplayName", "UninstallString", "InstallLocation", "DisplayIcon", "Publisher")}
                return e if e.get("DisplayName") else None
        except OSError:
            return None
    return None


# ---------------------------------------------------------------- 卸载：找残留
def _app_names(a):
    """拿来匹配文件夹的名字：去掉版本号、(x64)、语言之类的尾巴。"""
    raw = [a["name"], os.path.basename(a.get("location") or "")]
    if not re.fullmatch(r"\{[0-9A-Fa-f\-]{36}\}", a.get("bid") or "") and not re.search(r"_is1$", a.get("bid") or ""):
        raw.append(a.get("bid"))
    elif (a.get("bid") or "").endswith("_is1"):
        raw.append(a["bid"][:-4])
    names = set()
    for r in raw:
        r = re.sub(r"\(.*?\)|\[.*?\]", " ", str(r or ""))
        r = re.sub(r"\b(v?\d+([.\-]\d+)*[a-z]?|x64|x86|amd64|arm64|64-bit|32-bit|en-us|zh-cn)\b", " ", r, flags=re.I)
        n = _norm(r)
        if n:
            names.add(n)
    return names


def _publisher_norm(p):
    p = re.sub(r"[,.]?\s*(inc|corp|corporation|ltd|limited|llc|gmbh|co|company|s\.?a|ag|b\.?v)\.?$", "", str(p or ""), flags=re.I)
    return _norm(p)


def _match(child, names, pub):
    """返回 (理由, 默认勾不勾)，不匹配返回 None。只认名字完全一致，不做模糊匹配。"""
    base = os.path.splitext(child)[0] if child.lower().endswith((".lnk", ".url")) else child
    n = _norm(base)
    if not n or n in NEVER_CHILD or n in GENERIC_NAMES or len(n) < 4:
        return None
    if n in names:
        return ("名字一致", True)
    if pub and len(pub) >= 3 and any(n == pub + x or n == x + pub for x in names):
        return ("开发者名 + 应用名", True)
    return None


def find_leftovers(app_path, apps=None):
    """在用户和公共的数据文件夹里找这个应用留下的东西：只看第一层（发布者文件夹里多看一层），名字完全一致才算。"""
    apps = list_apps() if apps is None else apps
    a = next((x for x in apps if x["path"] == app_path), None) if isinstance(app_path, str) else app_path
    if not a:
        return {"bid": "", "team": "", "items": []}
    names = _app_names(a)
    pub = _publisher_norm(a.get("publisher"))
    others = [x for x in apps if x["path"] != a["path"]]
    other_names = set()
    for x in others:
        other_names |= _app_names(x)
    names = {n for n in names if n not in other_names}  # 另一个应用也叫这个名字，就不靠名字匹配
    other_dirs = [x["location"] for x in others if x.get("location")]
    found, seen = [], set()

    def add(p, why, on, admin=False):
        rp = _nc(os.path.realpath(p))
        if rp in seen or not os.path.lexists(p) or _is_reparse(p):
            return
        if any(_inside(d, p) or _inside(p, d) for d in other_dirs):
            return  # 和另一个已装应用的安装目录重叠，不碰
        why_not = _safe_target(p)
        if why_not and not why_not.startswith("需要管理员"):
            return
        seen.add(rp)
        found.append({"path": p, "why": why, "on": on and not admin, "admin": admin or bool(why_not)})

    locs = [(APPDATA, "%APPDATA%"), (LOCALAPPDATA, "%LOCALAPPDATA%"), (os.path.join(LOCALAPPDATA, "Programs"), "%LOCALAPPDATA%\\Programs"),
            (PROGRAMDATA, "%PROGRAMDATA%")]
    for d, label in locs:
        try:
            kids = os.listdir(d)
        except OSError:
            continue
        for k in kids:
            p = os.path.join(d, k)
            hit = _match(k, names, pub)
            if hit:
                add(p, hit[0] + " · " + label, hit[1])
                continue
            # 发布者文件夹（比如 %APPDATA%\JetBrains\PyCharm2024.1）：只看里面那层，发布者文件夹本身不动
            if pub and len(pub) >= 3 and _norm(k) == pub and os.path.isdir(p) and not _is_reparse(p):
                try:
                    inner = os.listdir(p)
                except OSError:
                    continue
                for k2 in inner:
                    h2 = _match(k2, names, "")
                    if h2:
                        add(os.path.join(p, k2), h2[0] + " · " + label + "\\" + k, h2[1])
    # 开始菜单、桌面上的快捷方式和文件夹
    for d in START_MENU + DESKTOPS:
        try:
            kids = os.listdir(d)
        except OSError:
            continue
        for k in kids:
            if not (k.lower().endswith((".lnk", ".url")) or os.path.isdir(os.path.join(d, k))):
                continue
            hit = _match(k, names, pub)
            if hit:
                add(os.path.join(d, k), hit[0] + " · " + ("开始菜单" if "Start Menu" in d else "桌面") + "快捷方式", True)
    # 家目录里的 .应用名 文件夹（默认不勾，可能有你自己的配置）
    try:
        for k in os.listdir(HOME):
            if k.startswith(".") and len(k) > 4 and _norm(k[1:]) in names:
                add(os.path.join(HOME, k), "名字一致 · 家目录里的隐藏文件夹（可能有你的配置）", False)
    except OSError:
        pass
    for pat, paths in FORCE:
        if re.search(pat, a["name"], re.I):
            for p in paths:
                p = os.path.expanduser(p.replace("%APPDATA%", APPDATA).replace("%LOCALAPPDATA%", LOCALAPPDATA))
                add(p, "这个应用已知会用的位置", not os.path.basename(p).startswith("."))
    # 安装目录：卸载程序一般会删掉，卸完还剩下的才移进回收站
    loc = a.get("location")
    if loc and os.path.isdir(loc):
        add(loc, "安装目录（卸载程序没删干净的部分）", True)
    return {"bid": a.get("bid", ""), "team": a.get("publisher", ""), "items": found}


# ---------------------------------------------------------------- 计划（先给人看、确认后才动）
PLANS = {}
_PLOCK = threading.Lock()
PLAN_TTL = 3600


def _prune_plans():
    for k in [k for k, v in PLANS.items() if time.time() - v["at"] > PLAN_TTL]:
        PLANS.pop(k, None)


def _save_run(moved):
    """记下这次移走了什么（存盘，Deck 重启后也能撤销）。只留最近 30 次。"""
    run_id = uuid.uuid4().hex[:12]
    with _SLOCK:
        runs = [r for r in (_load_state().get("runs") or []) if isinstance(r, dict)][-29:]
        runs.append({"id": run_id, "at": time.time(), "moved": moved})
        _save_state_locked({"runs": runs})
    return run_id


def _take_run(run_id):
    with _SLOCK:
        runs = [r for r in (_load_state().get("runs") or []) if isinstance(r, dict)]
        hit = next((r for r in runs if r.get("id") == run_id), None)
        if hit:
            _save_state_locked({"runs": [r for r in runs if r is not hit]})
    return hit


def _keep_plan(kind, items, extra=None):
    pid = uuid.uuid4().hex[:12]
    plan = {"id": pid, "kind": kind, "at": time.time(), "items": items}
    plan.update(extra or {})
    with _PLOCK:
        PLANS[pid] = plan
        _prune_plans()
    return plan


def _pub_items(items):
    return [{"i": i, "path": _home(it["path"]), "bytes": it.get("bytes", 0), "why": it.get("why", ""), "on": it.get("on", True),
             "admin": it.get("admin", False), "kind": it.get("kind", ""), "age_days": it.get("age_days")}
            for i, it in enumerate(items)]


def uninstall_plan(app_path):
    if not IS_WIN:
        return {"error": "只在 Windows 上能卸载应用"}
    apps = list_apps()
    a = next((x for x in apps if x["path"] == str(app_path or "")), None)
    if not a:
        return {"error": "找不到这个应用"}
    if a["protected"]:
        return {"error": "这是%s，不能卸载" % a["protected"]}
    e = _entry(a["path"]) or {}
    exe, _args = _split_cmd(e.get("UninstallString"))
    if not exe:
        return {"error": "这个应用没有登记卸载程序"}
    lo = find_leftovers(a, apps)
    shown = a.get("location") or (exe if os.path.isabs(exe) else "卸载程序：%s" % e.get("UninstallString"))
    items = [{"path": shown, "why": "应用本体：运行它自己的卸载程序（会弹出它的窗口，可能要管理员确认；卸载本身不能撤销）",
              "on": True, "admin": False, "kind": "app", "bytes": a.get("bytes") or 0}]
    loc = a.get("location")
    for it in lo["items"]:
        if loc and (_inside(it["path"], loc) or _inside(loc, it["path"])):
            # 安装目录：卸载程序会删掉大部分，卸完还剩下的才移进回收站；没卸就绝不动它
            it.update(kind="appdir", why="安装目录里卸载程序没删干净的部分（卸完才移，大小到时再算）")
    items += lo["items"]
    data_locs = [APPDATA, LOCALAPPDATA, PROGRAMDATA]
    for it in items[1:]:
        it["bytes"] = 0 if it.get("kind") == "appdir" else _du(it["path"], 15)
        if it["bytes"] > 100 * 1048576 and any(_inside(it["path"], d) for d in data_locs) and not it["why"].startswith("安装目录"):
            it["why"] += " · 可能有你的文档或数据库，请看一眼"
            if it["why"].startswith("名字一致"):
                it["on"] = False  # 只靠名字对上、又很大的，默认不勾
    plan = _keep_plan("uninstall", items, {"app": a})
    note = "先运行它自己的卸载程序；确认卸掉了，才把勾选的残留移进回收站。卸载程序取消了，残留一样都不动。"
    if any(i["admin"] for i in items):
        note += "需要管理员权限的项目不会自动处理，可以点「显示」在资源管理器里自己处理。"
    return {"id": plan["id"], "app": a, "team": lo["team"], "items": _pub_items(items), "note": note}


# ---------------------------------------------------------------- 运行卸载程序
class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p),
                ("hkeyClass", ctypes.c_void_p), ("dwHotKey", ctypes.c_uint32), ("hIconOrMonitor", ctypes.c_void_p),
                ("hProcess", ctypes.c_void_p)]


def _launch(exe, args):
    """用 ShellExecuteEx 打开卸载程序：需要管理员的会弹 UAC 让人确认。返回进程号。"""
    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x40 | 0x100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    sei.lpVerb = "open"
    sei.lpFile = exe
    sei.lpParameters = args or None
    sei.nShow = 1  # SW_SHOWNORMAL
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        raise OSError(err, "没打开卸载程序" + ("（你在管理员确认里点了「否」）" if err == 1223 else "（错误 %d）" % err))
    if not sei.hProcess:
        return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessId.argtypes = [ctypes.c_void_p]
    k32.GetProcessId.restype = ctypes.c_uint32
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    pid = k32.GetProcessId(sei.hProcess)
    k32.CloseHandle(sei.hProcess)
    return pid or None


def _wait_tree(pid, limit):
    """等卸载程序和它开出来的子进程都结束（很多卸载程序会把自己复制到临时文件夹再运行，原进程马上退出）。"""
    if not pid or not psutil:
        return True
    t0 = time.time()
    tracked = {}
    try:
        tracked[pid] = psutil.Process(pid)
    except psutil.Error:
        return True
    while time.time() - t0 < limit:
        try:
            for p in psutil.process_iter(["pid", "ppid", "create_time"]):
                info = p.info
                if info.get("ppid") in tracked and info["pid"] not in tracked and (info.get("create_time") or 0) >= t0 - 2:
                    tracked[info["pid"]] = p
        except psutil.Error:
            pass
        alive = [p for p in tracked.values() if p.is_running()]
        if not alive:
            return True
        time.sleep(0.25)
    return False


def _uninstall_cmd(e):
    exe, args = _split_cmd(e.get("UninstallString"))
    if os.path.basename(exe).lower() in ("msiexec.exe", "msiexec"):
        args = re.sub(r"(?i)/I\s*(\{[0-9A-F\-]{36}\})", r"/X\1", args)  # /I 是「修改」，换成卸载（仍然有界面）
    return exe, args


def execute(plan_id, picks):
    """执行清单里勾选的项目（picks 是序号列表）。只认服务端记下的路径。
    勾了应用本体：先运行它自己的卸载程序并等它结束，确认卸掉了才移残留；没卸掉就什么都不动。"""
    with _PLOCK:
        plan = PLANS.get(str(plan_id))
        if plan and plan["kind"] in ("uninstall", "files"):
            PLANS.pop(str(plan_id), None)
        else:
            plan = None
    if not plan or time.time() - plan["at"] > PLAN_TTL:
        return {"error": "清单过期了，请重新扫描"}
    try:
        idx = sorted({int(i) for i in (picks or [])})
    except (TypeError, ValueError):
        return {"error": "参数不对"}
    items = [plan["items"][i] for i in idx if 0 <= i < len(plan["items"])]
    items = [it for it in items if not it.get("admin")]
    if not items:
        return {"error": "没有勾选任何项目"}
    note = None
    if plan["kind"] == "uninstall":
        app = plan["app"]
        want_app = any(it.get("kind") == "app" for it in items)
        items = [it for it in items if it.get("kind") != "app"]
        if not want_app:
            items = [it for it in items if it.get("kind") != "appdir"]  # 应用还装着，安装目录不动
            if not items:
                return {"error": "没勾应用本体时不会动它的安装目录。要卸载请勾上「应用本体」。"}
        if want_app:
            if _is_running(app.get("location"), _running_exes()):
                with _PLOCK:
                    PLANS[plan["id"]] = plan
                return {"error": "「%s」还开着，请先退出它" % app["name"], "running": True}
            e = _entry(app["path"])
            if not e:
                return {"error": "「%s」已经不在应用列表里了（可能已经卸掉），请重新打开卸载应用看看" % app["name"]}
            exe, args = _uninstall_cmd(e)
            _log(["开始卸载 %s：运行 %s %s（你在工具箱里确认后执行）" % (app["name"], exe, args)])
            try:
                pid = _launch(exe, args)
            except OSError as ex:
                return {"error": ex.strerror or str(ex)}
            done = _wait_tree(pid, UNINSTALL_WAIT)
            for _ in range(20):  # 有的卸载程序最后才删登记，多等几秒
                if not _entry(app["path"]):
                    break
                time.sleep(0.5)
            if _entry(app["path"]):
                _log(["%s 的卸载程序结束了，但它还装着；残留没动" % app["name"]])
                return {"error": ("「%s」的卸载程序还没结束，残留先不动。卸完后再点一次「卸载…」可以清残留。" if not done else
                                  "「%s」看起来还装着（卸载被取消了，或者要重启才能完成）。残留一样都没动。") % app["name"]}
            note = "已用「%s」自己的卸载程序卸掉了它（这一步不能撤销）。" % app["name"]
            items = [it for it in items if os.path.lexists(it["path"])]  # 卸载程序已经删掉的不再列出
            for it in items:
                if it.get("kind") == "appdir":
                    it["bytes"] = _du(it["path"], 30)
            if not items:
                _log(["卸掉了 %s，没有要移的残留" % app["name"]])
                return {"run_id": None, "count": 0, "bytes": 0, "failed": [], "moved": [], "uninstalled": True,
                        "note": note + "没有剩下要清的残留。"}
        title = "移走 %s 的残留（你在工具箱里确认后执行）" % app["name"]
    else:
        title = "开始整理文件（你在工具箱里确认后执行）"
    res = _recycle_many(items, title)
    run_id = _save_run(res["moved"]) if res["moved"] else None
    out = {"run_id": run_id, "count": len(res["moved"]), "bytes": res["bytes"],
           "failed": res["failed"], "moved": [_home(m["src"]) for m in res["moved"]]}
    if note:
        out.update(uninstalled=True, note=note + "残留移进了回收站，可以撤销。")
    return out


def undo(run_id):
    run = _take_run(str(run_id))
    if not run:
        return {"error": "找不到这次操作的记录。东西还在回收站里，可以在回收站里右键「还原」。"}
    back, skipped = 0, []
    for m in run.get("moved") or []:
        try:
            common.restore(m.get("dst"), m["src"])
            back += 1
        except (OSError, KeyError):
            skipped.append(_home(m.get("src", "")))
    _log(["撤销：放回 %d 项" % back])
    return {"restored": back, "skipped": skipped}


def cancel(plan_id):
    with _PLOCK:
        PLANS.pop(str(plan_id), None)
    return {"ok": True}


def quit_app(app_path):
    """让它正常退出：taskkill 不加 /F，相当于点窗口的关闭按钮（会弹「要保存吗」），不强杀。"""
    a = next((x for x in list_apps() if x["path"] == str(app_path or "")), None)
    if not a or not a.get("location"):
        return {"error": "找不到这个应用"}
    if not psutil:
        return {"error": "没有 psutil，没法找它的进程"}
    pre = _nc(a["location"]).rstrip("\\/") + os.sep
    pids = []
    for p in psutil.process_iter(["pid", "exe"]):
        try:
            if p.info.get("exe") and _nc(p.info["exe"]).startswith(pre):
                pids.append(p.info["pid"])
        except psutil.Error:
            continue
    if not pids:
        return {"ok": True}
    cmd = ["taskkill"] + [x for pid in pids for x in ("/PID", str(pid))]
    code, out, err = _run(cmd, 20)
    return {"ok": code == 0, "error": None if code == 0 else "没能退出（它可能在后台运行，没有窗口）：%s" % (err or out).strip()[:120]}


def reveal(path):
    p = os.path.expanduser(str(path or ""))
    if not os.path.isabs(p) or "\x00" in p or not os.path.lexists(p):
        return {"error": "找不到"}
    if not IS_WIN:
        return {"error": "只在 Windows 上能在资源管理器里显示"}
    try:
        common.reveal(p)
    except OSError:
        return {"ok": False}
    return {"ok": True}


# ---------------------------------------------------------------- 垃圾清理（只移进回收站）
def _wcleaner():
    """Windows 版 cleaner（每日清理）。没有或拿到的是 Mac 版（路径都在 ~/Library 下）就返回 None。"""
    try:
        from winport import cleaner
        return cleaner
    except Exception:  # noqa: BLE001
        return None


def _x(p):
    """展开 ~、%TEMP%、%LOCALAPPDATA% 等；展不开（比如在 Linux 上）返回 None，免得按字面去找。"""
    c = _wcleaner()
    if c and hasattr(c, "_x"):
        return c._x(p)
    p = os.path.expandvars(os.path.expanduser(p.replace("%TEMP%", TEMP)))
    return None if "%" in p else os.path.normpath(p)


def _daily_rules():
    """每日清理（cleaner「缓存」组）的那几条规则，工具箱也列出来（两边共用一份清单，不另抄一遍）。"""
    c = _wcleaner()
    out = []
    for r in getattr(c, "RULES", []) if c else []:
        if r.get("group") != "缓存":
            continue
        paths = [x for x in (_x(p) for p in r.get("paths", [])) if x]
        if paths:
            out.append({"id": "daily-" + r["id"], "group": "每日清理清单", "label": r["label"], "paths": paths, "days": r.get("days", 0),
                        "on": r.get("on", True), "safe": bool(r.get("safe")), "skip_running_daily": r.get("skip_running")})
    return out


def weekly_whitelist():
    """每日清理（cleaner「缓存」组）那份缓存清单（已展开的路径）。"""
    return [p for r in _daily_rules() for p in r["paths"]]


def _busy(paths):
    """哪些路径底下有程序正开着的文件（用每日清理那份判断）。判断不了就当全部在用。"""
    c = _wcleaner()
    try:
        return set(c._in_use(list(paths)))
    except Exception:  # noqa: BLE001
        return set(paths)


def _rule_running(r):
    if r.get("skip_running") and _proc_running(r["skip_running"]):
        return True
    if r.get("skip_running_daily"):
        c = _wcleaner()
        try:
            return bool(c._running(r["skip_running_daily"]))
        except Exception:  # noqa: BLE001  不确定就当作在运行，不清
            return True
    return False


JUNK_EXCLUDE = ["_MEI*", "Deck*", "deck-*", "*.lock", "WinGet", "chocolatey", "*whisper*", "huggingface", "torch", "*model*",
                "*Model*", "ms-playwright*"]
CHROMIUM_CACHES = ("Cache", "Code Cache", "GPUCache")
BROWSERS = [  # (名字, 进程名, 配置文件夹的上一层, 每个配置里要清的文件夹)
    ("Chrome", "chrome.exe", os.path.join(LOCALAPPDATA, "Google", "Chrome", "User Data"), CHROMIUM_CACHES),
    ("Edge", "msedge.exe", os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data"), CHROMIUM_CACHES),
    ("Brave", "brave.exe", os.path.join(LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "User Data"), CHROMIUM_CACHES),
    ("Firefox", "firefox.exe", os.path.join(LOCALAPPDATA, "Mozilla", "Firefox", "Profiles"), ("cache2",)),
]
PROJECT_ROOTS = [os.path.join(HOME, n) for n in ("Desktop", "Documents", "source", "Projects", "projects", "code", "dev")]
DL = os.path.join(HOME, "Downloads")


def _g(base, *pat):
    """glob 模式：前半截是真实路径（转义），后半截是通配。"""
    return os.path.join(glob.escape(base), *pat)


def _junk_rules():
    rules = _daily_rules()
    rules += [
        {"id": "user-temp", "group": "系统垃圾", "label": "临时文件夹里 7 天没动过的东西（%TEMP%）", "paths": [_g(TEMP, "*")], "days": 7,
         "on": True, "exclude": JUNK_EXCLUDE, "busy_check": True},
        {"id": "crash-dumps", "group": "系统垃圾", "label": "程序崩溃时留下的转储文件（14 天前的）", "paths": [_g(LOCALAPPDATA, "CrashDumps", "*")],
         "days": 14, "on": True},
        {"id": "wer", "group": "系统垃圾", "label": "Windows 错误报告（30 天前的）",
         "paths": [_g(LOCALAPPDATA, "Microsoft", "Windows", "WER", d, "*") for d in ("ReportArchive", "ReportQueue")]
         + [_g(PROGRAMDATA, "Microsoft", "Windows", "WER", "ReportArchive", "*")], "days": 30, "on": True},
        {"id": "thumbcache", "group": "系统垃圾", "label": "缩略图缓存（资源管理器会重新生成；正在用的跳过）",
         "paths": [_g(LOCALAPPDATA, "Microsoft", "Windows", "Explorer", "thumbcache_*.db")], "days": 0, "on": False, "skip_in_use": True},
    ]
    for name, proc, base, leafs in BROWSERS:
        rules.append({"id": "browser-" + name.lower(), "group": "缓存", "label": "%s 网页缓存（只在 %s 没打开时清）" % (name, name),
                      "paths": [_g(base, "*", leaf) for leaf in leafs],
                      "days": 0, "on": True, "safe": True, "skip_running": [proc]})
    rules += [
        {"id": "dev-caches", "group": "缓存", "label": "开发工具的下载缓存（pip、npm、uv、yarn、electron 等，用到会自动重新下载）",
         "paths": [os.path.join(LOCALAPPDATA, *p) for p in (("pip", "Cache"), ("npm-cache", "_cacache"), ("uv", "cache"), ("Yarn", "Cache"),
                                                            ("pnpm", "store"), ("electron", "Cache"), ("electron-builder", "Cache"),
                                                            ("node-gyp", "Cache"))]
         + [os.path.join(HOME, ".cache", "uv"), os.path.join(APPDATA, "npm-cache", "_cacache")], "days": 0, "on": True, "safe": True},
        {"id": "partial-dl", "group": "下载", "label": "没下完的下载",
         "paths": [_g(DL, x) for x in ("*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload")], "days": 1, "on": True},
        {"id": "old-installers", "group": "下载", "label": "下载文件夹里 30 天前的安装包（exe / msi / msix）",
         "paths": [_g(DL, x) for x in ("*.exe", "*.msi", "*.msix", "*.msixbundle", "*.appx", "*.appxbundle")], "days": 30, "on": False},
        {"id": "dev-purge", "group": "开发", "label": "90 天没动的项目里的依赖和编译产物（node_modules、.next、target…，需要时重新安装）",
         "find": ["node_modules", ".next", "__pycache__", ".dart_tool", ".gradle", "target", ".build", "bin", "obj"],
         "dirs_only": True, "days": 90, "on": False, "purge": True},
    ]
    return rules


JUNK_RULES = _junk_rules()


def _excluded(p, pats):
    n = os.path.basename(p.rstrip("\\/"))
    return any(fnmatch.fnmatch(n, x) for x in pats or [])


def _proc_running(names):
    if not psutil:
        return True  # 不确定就当作在运行，不清
    want = {n.lower() for n in names}
    try:
        return any((p.info.get("name") or "").lower() in want for p in psutil.process_iter(["name"]))
    except psutil.Error:
        return True


def _file_in_use(p):
    """独占打开试一下：打不开（被资源管理器占着）就算在用。只读打开，不改文件。"""
    if not IS_WIN:
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.CreateFileW(p, 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ，不共享，OPEN_EXISTING
    if h in (None, ctypes.c_void_p(-1).value):
        return True
    k32.CloseHandle(h)
    return False


def _git_tracked(p):
    git = shutil.which("git")
    if not git:
        return False
    d = p if os.path.isdir(p) else os.path.dirname(p)
    code, _, _ = _run([git, "-C", d, "ls-files", "--error-unmatch", p], 5)
    return code == 0


def _find(patterns, dirs_only=False):
    out = []
    for root in PROJECT_ROOTS:
        if not os.path.isdir(root) or _is_reparse(root):
            continue
        base = root.rstrip("\\/").count(os.sep)
        for cur, dirs, files in os.walk(root):
            depth = cur.count(os.sep) - base
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".venv", "venv", "AppData")
                       and not _is_reparse(os.path.join(cur, d))]
            for name in list(dirs) + ([] if dirs_only else files):
                if any(fnmatch.fnmatch(name, pat) for pat in patterns):
                    out.append(os.path.join(cur, name))
                    if name in dirs:
                        dirs.remove(name)
            if depth >= 5:
                dirs[:] = []
    return out


def _purge_ok(p):
    """依赖目录：旁边得有对应的项目文件，才认定是可以重建的产物。"""
    d, n = os.path.dirname(p), os.path.basename(p)
    need = {"node_modules": ["package.json"], ".next": ["package.json"], "target": ["Cargo.toml", "pom.xml"],
            ".build": ["Package.swift"], ".dart_tool": ["pubspec.yaml"], ".gradle": ["build.gradle", "build.gradle.kts", "settings.gradle"],
            "__pycache__": [], "bin": ["*.csproj", "*.fsproj", "*.vbproj"], "obj": ["*.csproj", "*.fsproj", "*.vbproj"]}.get(n)
    if need is None:
        return False
    if os.sep + "node_modules" + os.sep in p:
        return False
    return not need or any(glob.glob(os.path.join(glob.escape(d), m)) for m in need)


def _scan(rules):
    """扫描，返回清理计划（不动任何文件）。结构和 Mac 版 cleaner.scan 一样。"""
    now = time.time()
    plan = {"id": uuid.uuid4().hex[:12], "at": now, "rules": []}
    seen = set()
    for r in rules:
        if _rule_running(r):
            continue
        if r.get("find"):
            cands = _find(r["find"], r.get("dirs_only"))
        else:
            cands = []
            for pat in r["paths"]:
                cands += glob.glob(os.path.expanduser(pat.replace("%TEMP%", TEMP)))
        cands = sorted(set(cands))
        if r.get("skip_in_use"):
            cands = [p for p in cands if not _file_in_use(p)]
        if r.get("busy_check") and cands:
            busy = _busy(cands)
            cands = [p for p in cands if p not in busy]
        items = []
        for p in cands:
            rp = _nc(os.path.realpath(p))
            if rp in seen or _is_reparse(p) or _safe_target(p):
                continue
            size, newest, n = _tree(p)
            if now - newest < r["days"] * DAY or size < r.get("min_bytes", 0):
                continue
            if r.get("find") and _git_tracked(p):
                continue
            seen.add(rp)
            items.append({"path": p, "bytes": size, "files": n, "age_days": int((now - newest) / DAY)})
        if items:
            items.sort(key=lambda i: -i["bytes"])
            plan["rules"].append({"id": r["id"], "group": r["group"], "label": r["label"], "on": r["on"], "days": r["days"],
                                  "safe": bool(r.get("safe")), "count": len(items), "bytes": sum(i["bytes"] for i in items),
                                  "examples": [_home(i["path"]) for i in items[:4]], "items": items})
    plan["bytes"] = sum(x["bytes"] for x in plan["rules"] if x["on"])
    return plan


def _public(plan):
    return {"id": plan["id"], "at": plan["at"], "bytes": plan["bytes"], "rules": [
        {k: r[k] for k in ("id", "group", "label", "on", "days", "count", "bytes", "examples")} for r in plan["rules"]]}


def junk_scan():
    global JUNK_RULES
    JUNK_RULES = _junk_rules()
    rules = JUNK_RULES
    plan = _scan(rules)
    # 去掉排除名单里的、以及被别的规则包含的（不重复算）
    taken = []
    for r in plan["rules"]:
        rule = next(x for x in rules if x["id"] == r["id"])
        keep = []
        for it in r["items"]:
            if _excluded(it["path"], rule.get("exclude")) or any(_inside(it["path"], t) or _inside(t, it["path"]) for t in taken):
                continue
            if rule.get("purge") and not _purge_ok(it["path"]):
                continue
            keep.append(it)
            taken.append(it["path"])
        r["items"] = keep
        r["count"] = len(keep)
        r["bytes"] = sum(i["bytes"] for i in keep)
        r["examples"] = [_home(i["path"]) for i in keep[:4]]
    plan["rules"] = [r for r in plan["rules"] if r["items"]]
    plan["bytes"] = sum(r["bytes"] for r in plan["rules"] if r["on"])
    with _PLOCK:
        _prune_plans()
        PLANS[plan["id"]] = {"id": plan["id"], "kind": "junk", "at": time.time(), "cplan": plan}
    pub = _public(plan)
    for r, src in zip(pub["rules"], plan["rules"]):
        r["items"] = [{"path": _home(i["path"]), "bytes": i["bytes"], "age_days": i["age_days"]} for i in src["items"][:200]]
    pub["trash_bytes"] = trash_size()
    return pub


def _junk_run(plan, rule_ids):
    moved, failed, logs = [], [], ["开始清理（工具箱，你确认后执行）"]
    for r in plan["rules"]:
        if r["id"] not in rule_ids:
            continue
        rule = next((x for x in JUNK_RULES if x["id"] == r["id"]), {})
        if _rule_running(rule):
            logs.append("跳过「%s」：相关应用正在运行" % r["label"])
            continue
        busy = _busy([it["path"] for it in r["items"]]) if rule.get("busy_check") else set()
        for it in r["items"]:
            src = it["path"]
            if not os.path.lexists(src) or src in busy or (rule.get("skip_in_use") and _file_in_use(src)):
                continue
            try:
                dst = to_trash(src)
                moved.append({"src": src, "dst": dst, "bytes": it["bytes"]})
                logs.append("移到回收站：%s -> %s (%dM)" % (src, dst or "?", round(it["bytes"] / 1048576)))
            except OSError as e:
                failed.append(src)
                logs.append("失败：%s（%s）" % (src, e.strerror or e))
    total = sum(m["bytes"] for m in moved)
    logs.append("完成：移走 %d 项，共 %dM；工具箱垃圾清理" % (len(moved), round(total / 1048576)))
    _log(logs)
    return {"moved": moved, "failed": failed, "bytes": total}


def junk_execute(plan_id, rule_ids):
    with _PLOCK:
        plan = PLANS.get(str(plan_id))
        if plan and plan.get("kind") == "junk":
            PLANS.pop(str(plan_id), None)
        else:
            plan = None
    if not plan or time.time() - plan["at"] > PLAN_TTL:
        return {"error": "清单过期了，请重新扫描"}
    ids = {str(x) for x in (rule_ids or [])}
    if not ids:
        return {"error": "没有勾选任何项目"}
    res = _junk_run(plan["cplan"], ids)
    run_id = _save_run(res["moved"]) if res["moved"] else None
    _save_state({"junk_last": {"at": time.time(), "bytes": res["bytes"], "count": len(res["moved"])}})
    return {"run_id": run_id, "count": len(res["moved"]), "bytes": res["bytes"], "failed": [_home(f) for f in res["failed"]]}


def trash_size():
    """回收站多大（当前用户、所有固定盘）。读不到返回 None。"""
    if not IS_WIN:
        return None
    try:
        return common.bin_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------- 文件管理：大文件、重复文件、空间分布
FILE_SKIP_DIRS = {"AppData", "node_modules", ".git", ".venv", "venv", ".cache", ".npm", ".cargo", ".rustup", ".gradle",
                  "$Recycle.Bin", "Application Data", "Local Settings", "My Documents", "NetHood", "PrintHood", "Recent",
                  "SendTo", "Start Menu", "Templates", "Cookies"}


def big_files(min_mb=200, limit=200):
    """走一遍用户文件夹（最多 40 秒）。不进 AppData、隐藏的点文件夹，不跟链接和联接。"""
    min_b = max(10, int(min_mb)) * 1048576
    items = []
    t0 = time.time()
    stack = [HOME]
    while stack and time.time() - t0 < 40:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                a = _attrs(st)
                if a & FILE_ATTRIBUTE_REPARSE_POINT and not a & (FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_OFFLINE):
                    continue  # 链接 / 联接；OneDrive 的在线文件也是重解析点，下面单独标出来
                if stat.S_ISLNK(st.st_mode):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if e.name in FILE_SKIP_DIRS or (d == HOME and e.name.startswith(".")) or a & FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                    stack.append(e.path)
                    continue
                if st.st_size >= min_b and _file_ok(e.path):
                    items.append({"path": e.path, "bytes": st.st_size, "mtime": st.st_mtime, "last_used": None,
                                  "dataless": bool(a & (FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN)),
                                  "ino": (st.st_dev, st.st_ino)})
    items.sort(key=lambda x: -x["bytes"])
    return items[:limit]


def _file_ok(p):
    if not _inside(p, HOME) or _inside(p, os.path.join(HOME, "AppData")):
        return False
    parts = os.path.relpath(p, HOME).split(os.sep)
    if parts and parts[0].startswith("."):
        return False
    return not any(x in FILE_SKIP_DIRS for x in parts[:-1])


def _quick_hash(p, full=False):
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            if full:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            else:
                h.update(f.read(1 << 20))
                f.seek(max(0, os.path.getsize(p) - (1 << 20)))
                h.update(f.read(1 << 20))
    except OSError:
        return None
    return h.hexdigest()


def duplicates(files):
    """在大文件里找内容完全一样的（先比大小，再比头尾，最后全文 SHA-256）。"""
    by_size, inos = {}, set()
    for f in files:
        # OneDrive 只在云端的文件不读（会触发下载）；同一个文件的硬链接只算一次
        if f.get("dataless") or f.get("ino") in inos:
            continue
        inos.add(f.get("ino"))
        by_size.setdefault(f["bytes"], []).append(f)
    groups = []
    for size, fs in by_size.items():
        if len(fs) < 2:
            continue
        by_q = {}
        for f in fs:
            by_q.setdefault(_quick_hash(f["path"]), []).append(f)
        for q, gs in by_q.items():
            if q is None or len(gs) < 2:
                continue
            by_full = {}
            for f in gs:
                by_full.setdefault(_quick_hash(f["path"], True), []).append(f)
            for h, g in by_full.items():
                if h and len(g) > 1:
                    groups.append(sorted(g, key=lambda f: f["mtime"]))
    return groups


def space_overview(progress=None):
    """家目录下第一层文件夹各占多少：4 个线程并行，整次最多 60 秒；超时的标「太大没算完」。"""
    try:
        names = os.listdir(HOME)
    except OSError:
        return []
    dirs = [os.path.join(HOME, n) for n in names]
    dirs = [p for p in dirs if os.path.isdir(p) and not _is_reparse(p)]
    deadline = time.time() + 60
    out, done = [], [0]
    lock = threading.Lock()

    def one(p):
        size, _, _ = _tree(p, limit=10 ** 8, deadline=deadline)
        b = None if time.time() > deadline else size
        with lock:
            done[0] += 1
            out.append({"path": _home(p), "bytes": b, "slow": b is None})
            if progress:
                progress("算到第 %d/%d 个文件夹" % (done[0], len(dirs)))

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, dirs))
    if IS_WIN:
        out.append({"path": "回收站", "bytes": trash_size()})
    out.sort(key=lambda x: -(x["bytes"] or 0))
    return out[:20]


def files_scan(min_mb=200):
    files = big_files(min_mb)
    dups = duplicates(files)
    now = time.time()
    items = []
    synced = [os.environ[k] for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if os.environ.get(k)]
    synced += [os.path.join(HOME, d) for d in ("OneDrive", "Dropbox", "Google Drive", "Nutstore Files", "iCloudDrive")]
    for f in files:
        used = f.get("last_used") or f["mtime"]
        why = "大文件" + (" · 只在云端，没下载到本机" if f.get("dataless") else "")
        if any(_inside(f["path"], d) for d in synced):
            why += " · 在同步文件夹里，移走后其他设备上也会没有"
        items.append({"path": f["path"], "bytes": f["bytes"], "why": why, "on": False, "kind": "big",
                      "age_days": int((now - used) / DAY)})
    dup_idx = []
    for g in dups:
        dup_idx.append([next(i for i, it in enumerate(items) if it["path"] == f["path"]) for f in g])
    plan = _keep_plan("files", items)
    return {"id": plan["id"], "items": _pub_items(items), "dups": dup_idx, "min_mb": min_mb}


# ---------------------------------------------------------------- 更新（winget）
def _vkey(v):
    parts = re.findall(r"\d+|[a-z]+", str(v or "").lower())
    return [(1, int(p), "") if p.isdigit() else (0, 0, p) for p in parts]


def newer(a, b):
    """a 比 b 新？"""
    if not a or not b:
        return False
    return _vkey(a) > _vkey(b)


WINGET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_\-]{0,127}")


def _winget_rows(text):
    """解析 `winget upgrade` 的表格。表头是本地化的（中文是「名称 ID 版本 可用 源」），中文名字在控制台里占两格，
    所以不按列宽切，而是从右往左取：源、可用版本、当前版本、ID 都不含空格，剩下的是名字。"""
    rows, cols, started = [], 0, False
    for raw in text.splitlines():
        line = raw.split("\r")[-1].rstrip()
        if not line.strip():
            if started:
                break  # 表格结束（后面可能是「需要明确指定才能升级」的另一张表，不列）
            continue
        if not started:
            if re.fullmatch(r"-{10,}", line.strip()):
                started = True
                continue
            toks = line.split()
            if 4 <= len(toks) <= 6 and any(t in ("Id", "ID") for t in toks):
                cols = len(toks)
            continue
        if line.endswith(":") or line.endswith("："):
            break  # 「下面这些要明确指定才能升级：」另一张表，不列
        toks = line.split()
        if len(toks) < 4:
            continue
        if cols >= 5 and toks[-1].lower() not in ("winget", "msstore"):
            continue  # 表格下面的说明文字（「n 个升级可用」之类）
        src = toks.pop() if cols >= 5 else "winget"
        avail = toks.pop()
        ver = toks.pop()
        if toks and toks[-1] in ("<", ">"):
            ver = toks.pop() + " " + ver
        if not toks:
            continue
        wid = toks.pop()
        if not WINGET_ID.fullmatch(wid):
            continue
        rows.append({"name": " ".join(toks) or wid, "token": wid, "from": ver, "to": avail, "source": src})
    return rows


def updates_check(progress=None):
    say = progress or (lambda s: None)
    res = {"at": time.time(), "brew": [], "formula": [], "sparkle": [], "mas": [], "unknown": [], "errors": [], "winget": bool(WINGET)}
    if not WINGET:
        res["note"] = "这台电脑没有 winget（应用安装程序），没法自动查更新。可以在 Microsoft Store 里装「应用安装程序」。"
        res["errors"].append(res["note"])
        _save_state({"updates": res})
        return res
    say("问 winget 有哪些能更新…")
    code, out, err = _run([WINGET, "upgrade", "--accept-source-agreements", "--disable-interactivity"], 180)
    rows = _winget_rows(out)
    for r in rows:
        if r["source"].lower() == "msstore":
            res["mas"].append({"name": r["name"], "path": r["token"], "from": r["from"], "to": r["to"], "id": r["token"], "notes": ""})
        else:
            res["brew"].append({"token": r["token"], "name": r["name"], "from": r["from"], "to": r["to"], "source": r["source"]})
    if not rows and code not in (0, -1978335212) and not re.search(r"-{10,}", out):  # 后面那个是「没有可用的升级」
        res["errors"].append("winget：%s" % ((err or out).strip()[-160:] or "没有返回结果"))
    _save_state({"updates": res})
    return res


def brew_upgrade(token, kind, progress=None):
    """Windows 上用 winget 更新一个应用：--interactive 让它的安装程序弹出界面，你看着它装。winget 不保留旧版。"""
    say = progress or (lambda s: None)
    if not WINGET or not isinstance(token, str) or not WINGET_ID.fullmatch(token):
        return {"error": "参数不对" if WINGET else "这台电脑没有 winget"}
    st = _load_state().get("updates") or {}
    if token not in {u.get("token") for u in st.get("brew", [])}:
        return {"error": "先检查一次更新"}
    say("winget upgrade %s…" % token)
    code, out, err = _run([WINGET, "upgrade", "--id", token, "--exact", "--interactive", "--accept-source-agreements",
                           "--accept-package-agreements"], 3600)
    _log(["更新 %s（winget）：%s" % (token, "成功" if code == 0 else "失败 %s" % code)])
    return {"ok": code == 0, "backup": None, "output": (out + err)[-1500:],
            "error": None if code == 0 else "更新失败，详情看输出", "note": "winget 更新不保留旧版"}


def open_update(kind, ref):
    """Microsoft Store 应用：打开商店页；winget 应用：开一个 winget 窗口更新它（安装程序有界面）。"""
    ref = str(ref or "")
    if kind == "mas":
        if not re.fullmatch(r"[A-Za-z0-9]{12}", ref):
            return {"error": "参数不对"}
        try:
            common.open_path("ms-windows-store://pdp/?ProductId=%s" % ref)
        except OSError:
            return {"ok": False}
        return {"ok": True}
    st = _load_state().get("updates") or {}
    hit = next((u for u in st.get("brew", []) if u.get("token") == ref), None)
    if not hit or not WINGET:
        return {"error": "找不到这个应用"}
    try:
        subprocess.Popen([WINGET, "upgrade", "--id", ref, "--exact", "--interactive"],  # noqa: S603  ref 已核对过
                         creationflags=CREATE_NEW_CONSOLE, stdin=subprocess.DEVNULL)
    except OSError as e:
        return {"error": "没打开 winget：%s" % e}
    return {"ok": True, "note": "已经开了一个 winget 窗口更新「%s」，按窗口里的提示操作" % (hit.get("name") or ref)}


# ---------------------------------------------------------------- 后台任务
JOBS = {}
_JLOCK = threading.Lock()


def start_job(kind, fn, *args):
    with _JLOCK:
        for j in JOBS.values():
            if j["kind"] == kind and j["state"] == "running":
                if kind == "upgrade" and j.get("args") != args:
                    return None  # 一次只升级一个
                return j["id"]  # 同一种任务同时只跑一个
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "kind": kind, "state": "running", "at": time.time(), "progress": "", "result": None, "args": args}
        JOBS[jid] = job
        for k in [k for k, v in JOBS.items() if v["state"] != "running" and time.time() - v["at"] > 3600]:
            JOBS.pop(k, None)

    def prog(s):
        job["progress"] = s

    def go():
        try:
            job["result"] = fn(*args, progress=prog) if kind in ("updates", "upgrade", "space") else fn(*args)
            job["state"] = "done"
        except Exception as e:  # noqa: BLE001
            job["result"] = {"error": "出错了：%s" % e}
            job["state"] = "error"
    threading.Thread(target=go, daemon=True).start()
    return jid


def job(jid):
    j = JOBS.get(str(jid))
    if not j:
        return {"error": "没有这个任务"}
    j = dict(j)
    j.pop("args", None)
    return j


def summary():
    """给监控台卡片的摘要（只读缓存，不做耗时操作）。"""
    st = _load_state()
    up = st.get("updates") or {}
    n = len(up.get("brew", [])) + len(up.get("sparkle", [])) + len(up.get("mas", []))
    return {"updates": n if up else None, "formula_updates": len(up.get("formula", [])) if up else None,
            "updates_at": up.get("at"), "update_names": [u.get("name") or u.get("token") for u in
                                                         (up.get("brew", []) + up.get("sparkle", []) + up.get("mas", []))][:6],
            "junk_last": st.get("junk_last"), "brew": bool(WINGET), "winget": bool(WINGET)}
