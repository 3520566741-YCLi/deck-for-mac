"""磁盘「谁在占空间」（Windows 版）。和 Mac 版 diskwatch.py 的函数名、返回的数据结构完全一样，页面和 Vesper 不用改。

三件事，全部只读、只在本机，不读文件内容，不联网：
- 谁在变大：每小时自己数一次文件夹大小（os.scandir，不用外部程序）：用户文件夹（深 3 层，含 AppData、下载、桌面、文档）、
  Program Files、Program Files (x86)、临时文件夹（%TEMP%，深 2 层）；存下 ≥50MB 的文件夹，和 1 小时 / 24 小时 / 7 天前比，
  列出涨得最多的，并按路径认出是哪个应用的（AppData\\Local\\<厂商>\\<应用>、Program Files\\<应用>）。
  数的时候把线程设成「后台模式」（Windows 会把它的 CPU 和磁盘读写都排到最后），每数 1000 个文件歇一下，最多数 500 万个、45 分钟；
  不进连接点 / 符号链接（不会绕圈、不会数到别的盘），OneDrive 里「只在云端」的文件按 0 算（本来就不占空间）。
  状态文件格式和 Mac 版一样（logs/disk-growth.json）。
- 最近新增：Windows 记着每个文件的「创建时间」，放进文件夹（下载、复制、解压）时就是那一刻。
  走一遍用户文件夹（下载、桌面、文档和其它不隐藏的文件夹，不进 AppData），找最近 7 天新建的；一次克隆 / 解压出来的一堆文件收拢成最外层那一个文件夹。
  来源按这个顺序判断：截图 / 录屏 → 下载标记（浏览器下载时写进文件的 Zone.Identifier，里面记着是从哪个网站下的）→ git 仓库 → 所在文件夹。
- 谁在写硬盘：psutil 读每个进程累计写了多少字节（任务管理器「I/O 写入字节」同一个数），每 30 秒采一次，按 exe 的「文件说明」归到应用上。
外置硬盘：U 盘、移动硬盘、SD 卡（按总线认，和「电脑实时状态」同一套），插上自动认，拔掉自动消失；第二块内置硬盘（D 盘之类）也列出来。
只用标准库 + ctypes + psutil。在 Linux 上（开发时 DECK_OS=win）也能导入、调用，只是数据少一些，不会报错。
"""
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import urllib.parse
from collections import deque

from . import common

try:
    import psutil
except ImportError:  # 开发机上没装也能导入
    psutil = None

IS_WIN = sys.platform == "win32"
SEP = os.sep
HOME = os.path.expanduser("~")


def _state_path():
    """和 Mac 版一样放在仓库的 logs/ 里；打包成 Deck.exe 后程序目录可能不能写，放到 %LOCALAPPDATA%\\Deck\\logs。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        return os.path.join(base, "Deck", "logs", "disk-growth.json")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "logs", "disk-growth.json")


STATE = _state_path()
SNAP_EVERY = 3600          # 系统盘每小时量一次
EXT_EVERY = 3 * 3600       # 别的盘 3 小时一次（大、慢）
KEEP_MIN = 50 * 1024 ** 2  # 快照里只存 ≥50MB 的目录
SHOW_MIN = 50 * 1024 ** 2  # 涨了 ≥50MB 才列出来
RECENT_DAYS = 7
MAX_ENTRIES = 5000000      # 一次快照最多数这么多个文件，数不完就放弃这次（不存半截的，免得比出假的涨跌）
MAX_SECONDS = 45 * 60
PAUSE_EVERY, PAUSE = 1000, 0.005  # 每数 1000 个歇 5 毫秒，让别的程序先用硬盘
TEMP = (os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()).rstrip("\\/")

# 文件属性（st_file_attributes）和重解析标记（st_reparse_tag）
_A_HIDDEN, _A_SYSTEM, _A_OFFLINE = 0x2, 0x4, 0x1000
_A_REPARSE = 0x400
_A_RECALL_ON_OPEN, _A_RECALL_ON_DATA = 0x40000, 0x400000

_lock = threading.Lock()
_state = None
_recent_cache = {}   # vol -> (时间, 结果)
_shown = {}          # 最近一次返回给页面的路径（「在资源管理器中显示」只认这些）
_shown_lock = threading.Lock()


# ---------------------------------------------------------------- 工具

def _norm(p):
    return os.path.normcase(p)


def _under(p, root):
    """p 是不是 root 本身或在 root 里面（Windows 不分大小写）。"""
    a, b = _norm(p), _norm(root).rstrip(SEP)
    return a == b or a.startswith(b + SEP)


def _tilde(p):
    if _norm(p) == _norm(HOME):
        return "~"
    return "~" + p[len(HOME):] if _under(p, HOME) else p


def _load():
    global _state
    if _state is None:
        try:
            with open(STATE, encoding="utf-8") as f:
                _state = json.load(f)
        except (OSError, ValueError):
            _state = {}
        if not isinstance(_state, dict):
            _state = {}
        _state.setdefault("snaps", {})
    return _state


def _save():
    st = _load()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, STATE)


def has_fda():
    """Mac 的「完全磁盘访问权限」。Windows 没有这种限制，自己的文件夹都能读，永远算有（页面就不提示去开权限）。"""
    return True


def _attrs(st):
    return getattr(st, "st_file_attributes", 0) or 0


def _is_cloud_tag(tag):
    return (tag & 0xFFFF0FFF) == 0x9000001A  # IO_REPARSE_TAG_CLOUD_*（OneDrive 等网盘）


def _skip_link(st):
    """连接点、符号链接、挂载点：不进去（会绕圈或数到别的盘）。网盘文件夹虽然也带重解析标记，但要进。"""
    if stat.S_ISLNK(st.st_mode):
        return True
    a = _attrs(st)
    if a & _A_REPARSE:
        return not _is_cloud_tag(getattr(st, "st_reparse_tag", 0) or 0)
    return False


def _fsize(st):
    """文件实际占多少：只在云端的网盘文件算 0；Linux 上按块算（和 du 一样）。"""
    a = _attrs(st)
    if a & (_A_OFFLINE | _A_RECALL_ON_OPEN | _A_RECALL_ON_DATA):
        return 0
    if hasattr(st, "st_blocks") and not IS_WIN:
        return st.st_blocks * 512
    return st.st_size


def _ctime(st):
    """创建时间（Windows 上就是放进这个文件夹的时间）。Python 3.12 起叫 st_birthtime。"""
    return getattr(st, "st_birthtime", None) or st.st_ctime


# ---------------------------------------------------------------- 后台模式（不抢 CPU 和硬盘）

_K32 = []


def _k32():
    if not _K32:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # 自己的一份，不改别的模块用的 windll 设置
        k.GetCurrentThread.restype = ctypes.c_void_p
        k.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k.GetVolumeInformationW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        _K32.append(k)
    return _K32[0]


def _background(on):
    """THREAD_MODE_BACKGROUND_BEGIN / END：这个线程的 CPU、磁盘、内存优先级都降到最低。只对当前线程有效。"""
    if not IS_WIN:
        return
    try:
        k = _k32()
        k.SetThreadPriority(k.GetCurrentThread(), 0x00010000 if on else 0x00020000)
    except (OSError, AttributeError):
        pass


# ---------------------------------------------------------------- 硬盘列表

def _vol_info(root):
    """(卷标, 序列号)。"""
    if not IS_WIN:
        return "", 0
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong(0)
        if _k32().GetVolumeInformationW(root, buf, 261, ctypes.byref(serial), None, None, None, 0):
            return buf.value, serial.value
    except (OSError, AttributeError):
        pass
    return "", 0


def _system_root():
    return (os.environ.get("SystemDrive", "C:") + "\\") if IS_WIN else "/"


def _drive_name(mount, label):
    letter = mount.rstrip("\\/")
    if not IS_WIN:
        return label or "系统盘"
    return "%s（%s）" % (label, letter) if label else "%s 盘" % letter.rstrip(":")


def _usage(path):
    try:
        if psutil:
            u = psutil.disk_usage(path)
            return u.total, u.free
        import shutil
        u = shutil.disk_usage(path)
        return u.total, u.free
    except OSError:
        return None


def _ext_list():
    try:
        from monitor import system_win
        return system_win.external_volumes()
    except Exception:  # noqa: BLE001  读不到外置盘不影响系统盘
        return []


_vol_cache = [0, None]


def volumes(max_age=10):
    """[{id, name, mount, total, free, pct, ext}]：第一个永远是系统盘（id 仍叫 "mac"，页面和接口默认就找它）。"""
    if _vol_cache[1] is not None and time.time() - _vol_cache[0] < max_age:
        return _vol_cache[1]
    vols = []
    sysroot = _system_root()
    sp = _usage(sysroot)
    if sp:
        t, f = sp
        vols.append({"id": "mac", "name": _drive_name(sysroot, _vol_info(sysroot)[0]), "mount": sysroot, "total": t, "free": f,
                     "pct": round(100.0 * (t - f) / t, 1) if t else None, "ext": False})
    ext = _ext_list()
    ext_mounts = {_norm(v.get("mount", "")) for v in ext}
    rows = []
    if IS_WIN and psutil:  # 第二块内置硬盘（D 盘之类）
        try:
            parts = psutil.disk_partitions(all=False)
        except Exception:  # noqa: BLE001
            parts = []
        for p in parts:
            opts = (p.opts or "").lower()
            if "fixed" not in opts or not p.fstype or _norm(p.mountpoint) in (_norm(sysroot),) or _norm(p.mountpoint) in ext_mounts:
                continue
            rows.append((p.mountpoint, False, p.fstype))
    for v in ext:
        rows.append((v.get("mount", ""), True, ""))
    seen = set()
    for mnt, is_ext, fs in rows:
        if not mnt or _norm(mnt) in seen:
            continue
        seen.add(_norm(mnt))
        sp = _usage(mnt)
        if not sp or sp[0] < 1024 ** 3:  # 小于 1GB 的多半是恢复分区、读卡器空槽
            continue
        t, f = sp
        label, serial = _vol_info(mnt)
        vid = "v%08x" % serial if serial else "v" + re.sub(r"[^0-9A-Za-z]", "", mnt)[-12:]
        vols.append({"id": vid, "name": _drive_name(mnt, label), "mount": mnt, "total": t, "free": f,
                     "pct": round(100.0 * (t - f) / t, 1) if t else None, "ext": is_ext, "fs": fs})
    _vol_cache[:] = [time.time(), vols]
    return vols


def _vol(vid):
    for v in volumes():
        if v["id"] == vid:
            return v
    return None


# ---------------------------------------------------------------- 谁的目录

def _pf_dirs():
    """Program Files 和 Program Files (x86)（按环境变量，不写死 C 盘）。"""
    out = []
    for k in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        p = os.environ.get(k)
        if p and os.path.isdir(p) and all(_norm(p) != _norm(x) for x in out):
            out.append(p.rstrip("\\/"))
    return out


# (路径（~ 开头是用户文件夹）, 应用名, 说明[, 怎么认下一层])；越具体的放越前面
KNOWN = [
    (("~", ".codex"), "Codex", "Codex 的数据和临时文件"),
    (("~", ".claude"), "Claude Code", "Claude Code 的会话和设置"),
    (("~", "AppData", "Roaming", "Claude"), "Claude", "Claude 桌面版"),
    (("~", "AppData", "Local", "AnthropicClaude"), "Claude", "应用本身"),
    (("~", "AppData", "Local", "claude-cli-nodejs"), "Claude Code", "缓存"),
    (("~", "Documents", "Codex"), "Codex", "Codex 的工作区"),
    (("~", ".cache", "uv"), "uv", "Python 包缓存 · 可以清"),
    (("~", "AppData", "Local", "uv", "cache"), "uv", "Python 包缓存 · 可以清"),
    (("~", ".cache", "huggingface"), "Hugging Face", "模型缓存"),
    (("~", ".cache", "pip"), "pip", "Python 包缓存 · 可以清"),
    (("~", "AppData", "Local", "pip", "Cache"), "pip", "Python 包缓存 · 可以清"),
    (("~", ".cache"), "命令行工具", "缓存"),
    (("~", ".npm"), "npm", "Node 包缓存 · 可以清"),
    (("~", "AppData", "Local", "npm-cache"), "npm", "Node 包缓存 · 可以清"),
    (("~", "AppData", "Roaming", "npm"), "npm", "全局安装的 Node 包"),
    (("~", "AppData", "Local", "pnpm"), "pnpm", "Node 包仓库"),
    (("~", ".pnpm-store"), "pnpm", "Node 包仓库"),
    (("~", "AppData", "Local", "Yarn"), "Yarn", "缓存"),
    (("~", ".bun"), "Bun", ""), (("~", ".cargo"), "Rust", ""), (("~", ".rustup"), "Rust", "工具链"),
    (("~", ".gradle"), "Gradle", "缓存"), (("~", ".m2"), "Maven", "缓存"), (("~", ".docker"), "Docker", ""),
    (("~", "AppData", "Local", "Docker"), "Docker", "虚拟磁盘和镜像"),
    (("~", ".ollama"), "Ollama", "本地模型"), (("~", ".lmstudio"), "LM Studio", "本地模型"),
    (("~", ".cursor"), "Cursor", ""), (("~", ".vscode"), "VS Code", "扩展"), (("~", ".dsh"), "DeepSeek Harness", ""),
    (("~", "AppData", "Roaming", "Code"), "VS Code", "设置和缓存"),
    (("~", "AppData", "Roaming", "Cursor"), "Cursor", "设置和缓存"),
    (("~", "AppData", "Local", "Google", "Chrome"), "Chrome", "浏览数据和缓存"),
    (("~", "AppData", "Local", "Microsoft", "Edge"), "Edge", "浏览数据和缓存"),
    (("~", "AppData", "Local", "Mozilla", "Firefox"), "Firefox", "缓存"),
    (("~", "AppData", "Roaming", "Mozilla", "Firefox"), "Firefox", "浏览数据"),
    (("~", "AppData", "Local", "Microsoft", "Teams"), "Teams", "缓存"),
    (("~", "AppData", "Local", "Microsoft", "Windows", "Explorer"), "资源管理器", "缩略图缓存 · 可以清"),
    (("~", "AppData", "Local", "Microsoft", "Windows", "INetCache"), "", "网页缓存 · 可以清"),
    (("~", "AppData", "Local", "Microsoft", "OneDrive"), "OneDrive", "应用本身"),
    (("~", "AppData", "Local", "CrashDumps"), "", "崩溃转储 · 可以清"),
    (("~", "AppData", "Local", "D3DSCache"), "显卡驱动", "着色器缓存 · 可以清"),
    (("~", "AppData", "Local", "NVIDIA", "DXCache"), "NVIDIA", "着色器缓存 · 可以清"),
    (("~", "AppData", "Local", "NVIDIA", "GLCache"), "NVIDIA", "着色器缓存 · 可以清"),
    (("~", "Documents", "WeChat Files"), "微信", "聊天文件"),
    (("~", "Documents", "xwechat_files"), "微信", "聊天文件"),
    (("~", "AppData", "Roaming", "Tencent", "WeChat"), "微信", ""),
    (("~", "Documents", "Tencent Files"), "QQ", "聊天文件"),
    (("~", "AppData", "Local", "JianyingPro"), "剪映", "草稿和缓存"),
    (("~", "AppData", "Local", "CapCut"), "CapCut", "草稿和缓存"),
    (("~", "OneDrive"), "OneDrive", "网盘（只在云端的文件不占空间）"),
    (("~", "iCloudDrive"), "iCloud 云盘", ""),
    (("~", "AppData", "Local", "Temp"), "临时文件", "你自己的临时文件夹，应用退出或清理时多半会删"),
    (("~", "AppData", "Local", "Packages"), "", "应用商店应用的数据", "pkg"),
    (("~", "AppData", "Local", "Programs"), "", "应用本身", "app"),
    (("~", "AppData", "LocalLow"), "", "应用数据", "vendor"),
    (("~", "AppData", "Local"), "", "应用数据和缓存", "vendor"),
    (("~", "AppData", "Roaming"), "", "应用数据", "vendor"),
    (("~", "AppData"), "", "应用数据"),
    (("~", "Downloads"), "", "下载"),
    (("~", "Desktop"), "", "桌面"),
    (("~", "Documents"), "", "文档"),
    (("~", "Pictures"), "", "图片"),
    (("~", "Videos"), "", "视频"),
    (("~", "Music"), "", "音乐"),
]
# 这些是厂商名：AppData\Local\Google\Chrome 这种，下一层才是应用
VENDORS = {"microsoft", "google", "mozilla", "adobe", "jetbrains", "tencent", "bytedance", "apple", "apple computer", "nvidia",
           "nvidia corporation", "intel", "amd", "autodesk", "unity", "unity technologies", "netease", "alibaba", "baidu",
           "kingsoft", "electron", "github", "logitech", "razer", "corsair", "asus", "lenovo", "dell", "hp"}


def _build_known():
    out = []
    for item in KNOWN:
        parts, app, desc = item[0], item[1], item[2]
        kind = item[3] if len(item) > 3 else ""
        p = os.path.join(HOME, *parts[1:]) if parts[0] == "~" else os.path.join(*parts)
        out.append((p, _norm(p), app, desc, kind))
    for pf in _pf_dirs():
        out.append((pf, _norm(pf), "", "应用" if "(x86)" not in pf else "应用（32 位）", "app"))
    return out


_KNOWN = _build_known()


def _pkg_name(s):
    """Microsoft.WindowsTerminal_8wekyb3d8bbwe → WindowsTerminal；5319275A.WhatsAppDesktop_cv1g… → WhatsAppDesktop。"""
    s = s.split("_")[0]
    segs = [x for x in s.split(".") if x and not re.match(r"^[0-9A-F]{6,}$", x)]
    return segs[-1] if segs else s


def owner(path):
    """路径 → (应用名, 说明)。"""
    path = str(path or "")
    np = _norm(path)
    for pre, npre, app, desc, kind in _KNOWN:
        if np == npre or np.startswith(npre + SEP):
            rest = path[len(pre) + 1:].split(SEP) if np != npre else []
            first = rest[0] if rest else ""
            if not app and first:
                if kind == "app":
                    return first, "应用本身"
                if kind == "pkg":
                    return _pkg_name(first), desc
                if kind == "vendor" and first.lower() in VENDORS and len(rest) > 1 and rest[1]:
                    return rest[1], desc
                return first, desc
            return app, desc
    if not _under(TEMP, HOME) and _under(path, TEMP):
        return "临时文件", "你自己的临时文件夹，应用退出或清理时多半会删"
    rel = path[len(HOME) + 1:] if _under(path, HOME) and np != _norm(HOME) else path
    first = rel.split(SEP)[0]
    if first.startswith("."):
        return first[1:] or first, "隐藏文件夹"
    return "", ""


# ---------------------------------------------------------------- 谁在变大

class _Budget:
    """一次数文件的上限：数太多或太久就停（stop=True）。"""

    def __init__(self, n, secs, pause=True):
        self.n, self.max, self.deadline, self.pause, self.stop = 0, n, time.time() + secs, pause, False

    def tick(self):
        self.n += 1
        if self.n % PAUSE_EVERY == 0:
            if self.pause:
                time.sleep(PAUSE)
            if self.n > self.max or time.time() > self.deadline:
                self.stop = True
        return self.stop


def _du(path, left, out, b, dev=None, lvl=0):
    """数 path 一共多大（字节）；深度还没到（left ≥ 0）且 ≥50MB 的文件夹记进 out。不进连接点 / 符号链接，不跨盘。"""
    if _norm(path) == _norm(TEMP):
        left = max(left, 2)  # 临时文件夹多看两层
    total = 0
    try:
        it = os.scandir(path)
    except OSError:
        return 0
    with it:
        for e in it:
            if b.tick():
                break
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if _skip_link(st):
                continue
            if stat.S_ISDIR(st.st_mode):
                if dev is not None and not IS_WIN and st.st_dev != dev:
                    continue  # Linux 上不跨文件系统（和 du -x 一样）
                if lvl < 200:
                    total += _du(e.path, left - 1, out, b, dev, lvl + 1)
            else:
                total += _fsize(st)
    if left >= 0 and total >= KEEP_MIN:
        out[path] = total
    return total


def _measure(root, depth, b):
    """量一个根目录：返回 {路径: 字节}，根目录本身一定在里面。"""
    out = {}
    try:
        dev = os.stat(root).st_dev
    except OSError:
        return out
    out[root] = _du(root, depth, out, b, dev)
    return out


def _key(vid, v, path):
    if vid == "mac":
        return _tilde(path)
    m = v["mount"].rstrip("\\/")
    return path[len(m):] or SEP


def snapshot(vid="mac"):
    """量一次（几分钟，在后台线程里跑，线程设成后台模式）。数不完（太多文件）就放弃这次，返回 None。"""
    v = _vol(vid)
    if not v:
        return None
    b = _Budget(MAX_ENTRIES, MAX_SECONDS)
    sizes = {}
    _background(True)
    try:
        if vid == "mac":
            sizes.update(_measure(HOME, 3, b))
            for pf in _pf_dirs():
                sizes.update(_measure(pf, 1, b))
            if TEMP and not _under(TEMP, HOME) and os.path.isdir(TEMP):
                sizes.update(_measure(TEMP, 2, b))
        else:
            sizes.update(_measure(v["mount"], 2, b))
    finally:
        _background(False)
    if b.stop or not sizes:
        return None
    snap = {"at": time.time(), "free": v["free"], "total": v["total"],
            "sizes": {_key(vid, v, k): s for k, s in sizes.items()}}
    with _lock:
        st = _load()
        lst = st["snaps"].setdefault(vid, [])
        lst.append(snap)
        # 留最近 30 小时的每一次 + 更早的每天一次（共 8 天）
        now = snap["at"]
        keep, days = [], set()
        for s in reversed(lst):
            age = now - s["at"]
            if age <= 30 * 3600:
                keep.append(s)
            elif age <= 8 * 86400:
                d = int(s["at"] // 86400)
                if d not in days:
                    days.add(d)
                    keep.append(s)
        st["snaps"][vid] = list(reversed(keep))
        st.setdefault("names", {})[vid] = v["name"]
        _save()
    return snap


def _pick(lst, ago):
    """最接近「ago 秒前」的那次快照（至少早于 ago 的 70%）。"""
    now = lst[-1]["at"]
    old = [s for s in lst[:-1] if now - s["at"] >= ago * 0.7]
    if not old:
        return None
    return min(old, key=lambda s: abs((now - s["at"]) - ago))


def _abs(vid, key):
    if vid == "mac":
        return os.path.join(HOME, key[2:]) if key.startswith("~" + SEP) else (HOME if key == "~" else key)
    v = _vol(vid)
    if not v:
        return key
    return v["mount"] if key == SEP else v["mount"].rstrip("\\/") + key


def growth(vid="mac", window=86400, n=15):
    with _lock:
        lst = list(_load()["snaps"].get(vid) or [])
    if not lst:
        return {"state": "waiting", "rows": []}
    cur = lst[-1]
    base = _pick(lst, window)
    out = {"state": "ok" if base else "first", "at": cur["at"], "rows": [], "next": cur["at"] + (SNAP_EVERY if vid == "mac" else EXT_EVERY)}
    if not base:
        return out
    out["base_at"] = base["at"]
    out["free_drop"] = base["free"] - cur["free"]
    a, b = cur["sizes"], base["sizes"]
    roots = {"~", SEP} | {_tilde(p) for p in _pf_dirs()} | ({_tilde(TEMP)} if TEMP and not _under(TEMP, HOME) else set())
    cand = {k: a[k] - b.get(k, 0) for k in a if k not in roots and a[k] - b.get(k, 0) >= SHOW_MIN}
    # 从最深的往上看：子目录已经列出来的那部分不再算到父目录头上；父目录剩下的（散在很多小文件夹里）够多才列一行「其它零散」
    acc, kept, extra = {}, {}, set()
    order = sorted(cand, key=lambda x: -x.count(SEP))
    for k in order:
        inner = [c for c in acc if c.startswith(k + SEP)]
        near = [c for c in inner if not any(o != c and c.startswith(o + SEP) for o in inner)]
        rest = cand[k] - sum(acc[c] for c in near)
        if rest >= SHOW_MIN and rest >= cand[k] * 0.3:
            kept[k] = rest
            if near:
                extra.add(k)
        acc[k] = cand[k]
    v = _vol(vid) if vid != "mac" else None
    rows = []
    for k, d in sorted(kept.items(), key=lambda x: -x[1])[:n]:
        full = _abs(vid, k)
        if vid == "mac":
            app, desc = owner(full)
        else:
            app, desc = "", "外置盘文件夹" if (v and v.get("ext")) else "文件夹"
        if k in extra:
            desc = "其它零散的（上面单列的不算在内）"
        rows.append({"path": k, "full": full, "name": os.path.basename(k.rstrip(SEP)) or k, "app": app, "desc": desc,
                     "size": a[k], "delta": d, "new": k not in b})
    out["rows"] = rows
    _remember(vid, [r["full"] for r in rows])
    return out


# ---------------------------------------------------------------- 最近新增

SKIP_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".build", "Pods", "AppData", "$RECYCLE.BIN",
              "System Volume Information", "WindowsApps"}
RECENT_MAX = 400000   # 一次最多看这么多个文件
RECENT_SECS = 60


def _zone(path):
    """下载标记 Zone.Identifier（浏览器、聊天软件下载时写进文件的附加数据流）。
    没有 → None；有 → 下载的网站（HostUrl / ReferrerUrl 的域名，看不出时是空字符串）。"""
    if not IS_WIN:
        return None
    try:
        with open(path + ":Zone.Identifier", "rb") as f:
            raw = f.read(8192)
    except OSError:
        return None
    try:
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "ignore")
    urls = {}
    for line in text.splitlines():
        k, _, val = line.partition("=")
        urls[k.strip().lower()] = val.strip()
    return _host([urls.get("hosturl"), urls.get("referrerurl")])


def _host(urls):
    for u in urls or []:
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            h = urllib.parse.urlsplit(u).hostname or ""
            return h[4:] if h.startswith("www.") else h
    return ""


_SHOT_DIRS = {"screenshots", "屏幕截图", "截图"}
_REC_DIRS = {"captures", "screen recordings", "屏幕录制", "捕获"}


def source(path, is_dir, md=None, qa=None):
    """→ (徽章文字, 颜色类, 一句说明)。颜色类：w 下载、g 代码/聊天、x 截图/系统、空 = 应用。
    md 只为和 Mac 版参数一致（Windows 没有 Spotlight，不用）；qa 是 _zone() 的结果。"""
    name = os.path.basename(path)
    parent = os.path.basename(os.path.dirname(path))
    if not is_dir and (re.match(r"^(屏幕截图|Screenshot)[ _(]", name, re.I) or parent.lower() in _SHOT_DIRS):
        return "截图", "x", "Windows 截图"
    if not is_dir and (re.match(r"^(屏幕录制|Screen Recording)[ _(]", name, re.I) or parent.lower() in _REC_DIRS):
        return "录屏", "x", "Windows 录屏"
    if qa is not None:
        if qa:
            return "下载", "w", "从 %s 下载" % qa
        return "下载", "w", "从网上下载的"
    if is_dir and os.path.exists(os.path.join(path, ".git")):
        return "git 克隆", "g", "代码仓库"
    app, desc = owner(path)
    if app:
        return app, "", desc or "在 %s 的文件夹里" % app
    return "不确定", "x", "在「%s」文件夹里" % parent if parent else "看不出来源"


_recent_busy = set()


def recent(vid="mac"):
    """走一遍文件夹要好几秒，所以放到后台线程里查：2 分钟内的结果直接给；过期了先给旧的、后台重查（loading=True，页面过 2 秒再来拿）。"""
    hit = _recent_cache.get(vid)
    fresh = hit and time.time() - hit[0] < 120
    if not fresh and vid not in _recent_busy:
        _recent_busy.add(vid)

        def job():
            _background(True)
            try:
                res = _recent(vid)
                _recent_cache[vid] = (time.time(), res)
            except Exception as e:  # noqa: BLE001
                _recent_cache[vid] = (time.time(), {"error": "没查成：%s" % type(e).__name__, "rows": []})
            finally:
                _background(False)
                _recent_busy.discard(vid)
        threading.Thread(target=job, name="disk-recent", daemon=True).start()
    if hit:
        return dict(hit[1], loading=vid in _recent_busy)
    return {"loading": True, "rows": []}


def _known_folder(guid):
    """下载 / 桌面 / 文档可能被挪到别处（比如 D 盘或 OneDrive 里），问系统要真实位置。"""
    if not IS_WIN:
        return None
    try:
        import ctypes
        import uuid
        b = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        p = ctypes.c_wchar_p()
        sh = ctypes.WinDLL("shell32")
        sh.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        if sh.SHGetKnownFolderPath(ctypes.byref(b), 0, None, ctypes.byref(p)) != 0:
            return None
        res = p.value
        ctypes.WinDLL("ole32").CoTaskMemFree(p)
        return res
    except (OSError, AttributeError, ValueError):
        return None


_KF = ("374DE290-123F-4565-9164-39C4925E467B",  # 下载
       "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",  # 桌面
       "FDD39AD0-238F-46AF-ADB4-6C85480369C7")  # 文档


def _hidden(e, st):
    return e.name.startswith(".") or bool(_attrs(st) & (_A_HIDDEN | _A_SYSTEM))


def _roots(vid, v):
    """只查用户自己的文件夹：用户文件夹下不隐藏、不是 AppData 的那些，加上被挪走的下载 / 桌面 / 文档。缓存看「谁在变大」。"""
    if vid != "mac":
        return [v["mount"]]
    out = []
    try:
        for e in os.scandir(HOME):
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode) and not _skip_link(st) and not _hidden(e, st) and e.name not in SKIP_PARTS:
                out.append(e.path)
    except OSError:
        pass
    for g in _KF:
        p = _known_folder(g)
        if p and os.path.isdir(p) and not any(_under(p, r) for r in out):
            out.append(p)
    return out


def _scan_new(root, cutoff, found, b):
    """root 下面创建时间晚于 cutoff 的文件和文件夹 → found[路径] = (是不是文件夹, 大小, 创建时间)。"""
    stack = [root]
    while stack and not b.stop:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if b.tick():
                    break
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _skip_link(st) or _hidden(e, st) or e.name in SKIP_PARTS:
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                t = _ctime(st)
                if t >= cutoff:
                    found[e.path] = (is_dir, 0 if is_dir else _fsize(st), t)
                if is_dir:
                    stack.append(e.path)


_git_cache = {}


def _git_root(p, stop):
    """p 所在的 git 仓库根目录（往上找 .git，结果按目录缓存）；不在仓库里返回 ""。"""
    d = os.path.dirname(p)
    seen = []
    root = ""
    stops = {_norm(HOME), _norm(stop or "")}
    while d and os.path.dirname(d) != d and _norm(d) not in stops:
        if d in _git_cache:
            root = _git_cache[d]
            break
        seen.append(d)
        if os.path.exists(os.path.join(d, ".git")):
            root = d
            break
        d = os.path.dirname(d)
    for x in seen:
        _git_cache[x] = root
    return root


def _dir_size(p):
    b = _Budget(200000, 10)
    return _du(p, -1, {}, b)


def _recent(vid="mac", days=RECENT_DAYS, n=60):
    """最近 days 天新放进来的文件，一次克隆/解压的收拢成最外层文件夹。"""
    v = _vol(vid)
    if not v:
        return {"error": "这块硬盘已经拔掉了", "rows": []}
    t0 = time.time()
    cutoff = t0 - days * 86400
    _git_cache.clear()
    roots = _roots(vid, v)
    found = {}
    b = _Budget(RECENT_MAX, RECENT_SECS)
    for r in roots:
        _scan_new(r, cutoff, found, b)
    paths = list(found)
    pset = set(paths)
    stops = {_norm(x) for x in roots} | {_norm(HOME), _norm(v["mount"])}
    tops = {}  # 最外层路径 -> [大小, 文件数, 最新时间]
    repos = set()
    for p in paths:
        is_dir, size, t = found[p]
        # 代码仓库里的新文件（拉代码、编译、AI 改代码）收成「这个仓库」一行，不然整页都是项目里的零碎文件
        top = _git_root(p, v["mount"]) or None
        if top:
            repos.add(top)
        else:
            top, cur = p, os.path.dirname(p)
            while cur and os.path.dirname(cur) != cur and _norm(cur) not in stops:
                if cur in pset:
                    top = cur
                cur = os.path.dirname(cur)
        rec = tops.setdefault(top, [0, 0, 0.0])
        if not is_dir:
            rec[0] += size
            rec[1] += 1
        rec[2] = max(rec[2], t)
    items = sorted(tops.items(), key=lambda x: -x[1][2])
    by_time = items[:n]
    by_size = sorted(items, key=lambda x: -x[1][0])[:n]
    pick = dict(by_time + by_size)
    rel = (lambda d: _tilde(d)) if vid == "mac" else (lambda d: d)

    def build(kv):
        p, (size, cnt, t) = kv
        try:
            st = os.lstat(p)
        except OSError:
            return None
        is_dir = stat.S_ISDIR(st.st_mode)
        if is_dir and cnt == 0:
            size = _dir_size(p)
        elif not is_dir:
            size = _fsize(st)
        added = found[p][2] if p in found else t
        if p in repos:
            cloned = p in pset
            badge, cls = ("git 克隆", "g") if cloned else ("代码仓库", "g")
            desc = ("新克隆的仓库" if cloned else "仓库里新增或重建了 %d 个文件" % cnt)
            return {"path": rel(os.path.dirname(p)), "full": p, "name": os.path.basename(p), "dir": True, "size": size,
                    "added": added if cloned else t, "src": badge, "cls": cls, "desc": desc}
        qa = None if is_dir else _zone(p)
        if is_dir:
            # 文件夹本身没有下载标记（比如解压出来的）：看里面最多 5 个文件，都是同一个网站下载的才算它的
            hosts = []
            try:
                for e in os.scandir(p):
                    if e.is_file(follow_symlinks=False) and not e.name.startswith("."):
                        hosts.append(_zone(e.path))
                        if len(hosts) >= 5:
                            break
            except OSError:
                pass
            if hosts and hosts[0] is not None and all(h == hosts[0] for h in hosts):
                qa = hosts[0]
        badge, cls, desc = source(p, is_dir, {}, qa)
        if is_dir and cnt > 1 and badge not in ("git 克隆",):
            desc = desc + " · %d 个文件" % cnt
        elif is_dir and cnt > 1:
            desc = "文件夹 · %d 个文件" % cnt
        return {"path": rel(os.path.dirname(p)), "full": p, "name": os.path.basename(p), "dir": is_dir, "size": size,
                "added": added, "src": badge, "cls": cls, "desc": desc}

    rows = [r for r in (build(kv) for kv in pick.items()) if r]
    res = {"rows": rows, "days": days, "found": len(paths), "took": round(time.time() - t0, 1), "at": time.time()}
    if b.stop:
        res["note"] = "文件太多，只看了一部分。"
    _remember(vid, [r["full"] for r in rows])
    return res


# ---------------------------------------------------------------- 谁在写硬盘

def _file_description(path):
    try:
        from .procs import _file_description as fd  # 进程详情那边已经写好的「文件说明」读取（带缓存）
        return fd(path) or ""
    except Exception:  # noqa: BLE001
        return ""


class Writers:
    """每 30 秒记一次每个进程累计写了多少字节，留 1 小时。"""
    EVERY = 30

    def __init__(self):
        self.samples = deque(maxlen=int(3600 / self.EVERY) + 2)
        self.names = {}
        self.lib = psutil  # 和 Mac 版一样：没有就什么都不采

    def _app(self, pid, proc=None):
        if pid in self.names:
            return self.names[pid]
        path, pname = "", ""
        try:
            proc = proc or psutil.Process(pid)
            pname = proc.name() or ""
            path = proc.exe() or ""
        except Exception:  # noqa: BLE001  别的用户的进程读不到路径
            pass
        name = _file_description(path) if path else ""
        if not name:
            base = os.path.basename(path) or pname
            name = re.sub(r"\.exe$", "", base, flags=re.I) if base else "进程 %d" % pid
        windir = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
        sysd = (not path) or (IS_WIN and _under(path, windir)) or (not IS_WIN and path.startswith(("/usr/sbin/", "/sbin/", "/usr/lib/")))
        self.names[pid] = (name[:60], bool(sysd))
        return self.names[pid]

    def sample(self):
        if not self.lib:
            return
        cur = {}
        procs = {}
        try:
            it = list(psutil.process_iter())
        except Exception:  # noqa: BLE001
            return
        for p in it:
            if p.pid == 0:
                continue
            try:
                io = p.io_counters()
            except Exception:  # noqa: BLE001  读不到（别的用户、刚退出）就跳过
                continue
            cur[p.pid] = int(getattr(io, "write_bytes", 0) or 0)
            procs[p.pid] = p
        # pid 会被复用：消失的 pid 把名字也忘掉
        for pid in list(self.names):
            if pid not in cur:
                del self.names[pid]
        for pid in cur:
            if pid not in self.names:
                self._app(pid, procs.get(pid))
        self.samples.append((time.time(), cur))

    def top(self, window=600, n=12):
        if not self.samples:
            try:
                self.sample()
            except Exception:  # noqa: BLE001
                pass
        samples = list(self.samples)  # 采样线程同时在 append，先拷一份
        if not samples:
            now = time.time()
            return {"rows": [], "span": None, "since": now, "at": now}
        now, cur = samples[-1]
        base = None
        for t, s in samples:
            if now - t <= window + self.EVERY / 2:
                base = (t, s)
                break
        apps = {}
        for pid, w in cur.items():
            if window == 0:
                d = w
            elif base and base[1] is not cur:
                d = w - base[1][pid] if pid in base[1] else w
            else:
                d = 0
            name, sysd = self.names.get(pid) or self._app(pid)
            a = apps.setdefault(name, {"name": name, "sys": sysd, "bytes": 0, "total": 0, "procs": 0})
            a["bytes"] += max(0, d)
            a["total"] += w
            a["procs"] += 1
        rows = sorted(apps.values(), key=lambda a: -a["bytes"])
        rows = [r for r in rows if r["bytes"] > 0][:n]
        span = (now - base[0]) if base and window else None
        return {"rows": rows, "span": span, "since": samples[0][0], "at": now}


WRITERS = Writers()


# ---------------------------------------------------------------- 在资源管理器中显示

def _remember(vid, paths):
    with _shown_lock:
        s = _shown.setdefault(vid, deque(maxlen=400))
        for p in paths:
            s.append(p)


def reveal(path):
    """只认最近返回给页面过的路径，不能用来打开任意东西；只在资源管理器里选中，不运行。"""
    path = str(path or "")
    with _shown_lock:
        ok = bool(path) and any(path in s for s in _shown.values())
    if not ok or not os.path.lexists(path):
        return {"error": "这个文件已经不在了，或者不在列表里"}
    if not IS_WIN:
        return {"ok": False, "error": "只有在 Windows 上能打开资源管理器"}
    try:
        common.reveal(path)
    except Exception as e:  # noqa: BLE001
        return {"error": "打开资源管理器失败：%s" % e}
    return {"ok": True, "error": None}


# ---------------------------------------------------------------- 总览 + 后台线程

def overview(vid="mac"):
    vols = volumes()
    v = next((x for x in vols if x["id"] == vid), vols[0] if vols else None)
    vid = v["id"] if v else "mac"
    with _lock:
        lst = list(_load()["snaps"].get(vid) or [])
    day = _pick(lst, 86400) if lst else None
    g = growth(vid, 86400, 3)
    top = g["rows"][0] if g.get("rows") else None
    return {"vols": [{k: x[k] for k in ("id", "name", "total", "free", "pct", "ext")} for x in vols], "vid": vid,
            "day_drop": (day["free"] - v["free"]) if day and v else None, "day_span": (lst[-1]["at"] - day["at"]) if day else None,
            "top": top and {"app": top["app"] or top["name"], "delta": top["delta"]},
            "snap_at": lst[-1]["at"] if lst else None, "fda": has_fda()}


_started = []


def start():
    if _started:
        return
    _started.append(True)

    def loop():
        time.sleep(90)  # 等服务器起来、开机那阵忙过去
        last = {}
        while True:
            try:
                WRITERS.sample()
            except Exception:  # noqa: BLE001
                pass
            now = time.time()
            try:
                vols = volumes(max_age=0)
            except Exception:  # noqa: BLE001
                vols = []
            for v in vols:
                every = SNAP_EVERY if v["id"] == "mac" else EXT_EVERY
                if not last.get(v["id"]):
                    with _lock:
                        lst = _load()["snaps"].get(v["id"]) or []
                    last[v["id"]] = lst[-1]["at"] if lst else 0
                if now - last[v["id"]] >= every and v["id"] not in _snapping:
                    last[v["id"]] = now
                    threading.Thread(target=_safe_snap, args=(v["id"],), name="disk-snap", daemon=True).start()
            time.sleep(Writers.EVERY)
    threading.Thread(target=loop, name="diskwatch", daemon=True).start()


_snapping = set()
_snap_one = threading.Lock()  # 一次只量一块盘，不同时压好几块硬盘


def _safe_snap(vid):
    if vid in _snapping:
        return
    _snapping.add(vid)
    try:
        with _snap_one:
            snapshot(vid)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _snapping.discard(vid)
