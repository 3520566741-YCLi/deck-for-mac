"""Vesper 找文件、看文件夹、在资源管理器里打开（Windows 版，只读，不改不删任何文件）。

和 Mac 版 files.py 同一套接口和返回格式；名字匹配、模糊匹配、目录扫描直接复用 Mac 版的代码，
这里只换掉 Windows 不一样的地方：
- 桌面 / 文档 / 下载的真实位置问系统（开了 OneDrive 备份时桌面在 OneDrive\\桌面 里）；
- 只在用户文件夹里活动，跳过 AppData（相当于 Mac 的资源库）；
- 打开用系统默认程序，「选中」用资源管理器；不运行 exe、bat、ps1、msi、快捷方式这类。
- 没有 Spotlight：只做目录扫描（Windows 搜索索引的接口要 COM，以后需要再加）。
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import uuid

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("_files_mac", os.path.join(_HERE, "files.py"))
_mac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mac)

IS_WIN = sys.platform == "win32"
HOME = os.path.expanduser("~")

# 已知文件夹的 GUID（SHGetKnownFolderPath）
_FOLDERS = {"desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
            "downloads": "374DE290-123F-4565-9164-39C4925E467B"}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _known(key, fallback):
    if not IS_WIN:
        return fallback
    try:
        u = uuid.UUID(_FOLDERS[key])
        g = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            if path and os.path.isdir(path):
                return path
    except (OSError, ValueError, AttributeError):
        pass
    return fallback


PLACES = {
    "desktop": _known("desktop", os.path.join(HOME, "Desktop")),
    "documents": _known("documents", os.path.join(HOME, "Documents")),
    "downloads": _known("downloads", os.path.join(HOME, "Downloads")),
    "home": HOME,
}
ALIASES = dict(_mac.ALIASES, **{"文件": "documents", "我的文档": "documents"})
DEPTH = _mac.DEPTH
SKIP_DIRS = _mac.SKIP_DIRS | {"AppData", "OneDrive - Personal", "$Recycle.Bin", "System Volume Information", "MicrosoftEdgeBackups"}
NO_RUN = {e.lower() for e in _mac.NO_RUN} | {".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                                             ".msi", ".msix", ".appx", ".lnk", ".reg", ".scr", ".com", ".cpl", ".hta",
                                             ".msc", ".pif", ".application", ".appref-ms", ".dll", ".sys"}
PERM_HINT = "Deck 没有读这个文件夹的权限（可能被「受控文件夹访问」或别的安全软件挡住了）。可以在 Windows 安全中心 → 病毒和威胁防护 → 勒索软件防护里允许 Deck。"

# 复用 Mac 版的工具函数，但让它们看到 Windows 的设置
_norm, _human, _score, _info = _mac._norm, _mac._human, _mac._score, _mac._info
_mac.HOME, _mac.PLACES, _mac.SKIP_DIRS = HOME, PLACES, SKIP_DIRS


def _tilde(p):
    return "~" + p[len(HOME):] if os.path.normcase(p) == os.path.normcase(HOME) or \
        os.path.normcase(p).startswith(os.path.normcase(HOME + os.sep)) else p


def _inside_home(p):
    """在用户文件夹里（或 OneDrive 重定向的桌面 / 文档 / 下载里），而且不在 AppData 里。"""
    p = os.path.normcase(os.path.normpath(os.path.abspath(p)))
    roots = {os.path.normcase(os.path.normpath(r)) for r in PLACES.values()}
    appdata = os.path.normcase(os.path.join(os.path.normpath(HOME), "AppData"))
    if p == appdata or p.startswith(appdata + os.sep):
        return False
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def _resolve(path):
    if not path:
        return None
    path = path.strip()
    key = ALIASES.get(path.lower()) or ALIASES.get(path)
    if key:
        return PLACES[key]
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(HOME, path)
    return os.path.normpath(path)


_mac._tilde, _mac._inside_home, _mac._resolve = _tilde, _inside_home, _resolve
_mac._mdfind = lambda query, limit=40: []  # 没有 Spotlight
_mac.PERM_HINT = PERM_HINT


def find_files(name, where=None, limit=12):
    return _mac.find_files(name, where, limit)


def list_folder(path, limit=80):
    out = _mac.list_folder(path, limit)
    if isinstance(out, dict) and out.get("error", "").startswith("只能看家目录"):
        out["error"] = "只能看用户文件夹里的东西（不含 AppData）"
    return out


def open_path(path, reveal=False):
    p = _resolve(path)
    if not p or not _inside_home(p):
        return {"error": "只能打开用户文件夹里的东西（不含 AppData）"}
    if not os.path.exists(p):
        return {"error": "不存在：%s（先用 find_files 找到准确路径）" % _tilde(p)}
    is_dir = os.path.isdir(p)
    ext = os.path.splitext(p)[1].lower()
    if not reveal and not is_dir and ext in NO_RUN:
        return {"error": "这是程序、脚本、安装包或快捷方式，不直接运行。可以在资源管理器里选中它（reveal=true），应用用 screen_open。"}
    if not IS_WIN:
        return {"error": "只在 Windows 上能打开"}
    try:
        if reveal:
            subprocess.Popen(["explorer.exe", "/select,", p])  # noqa: S603,S607
        else:
            os.startfile(p)  # noqa: S606  只打开文件夹和文稿；程序、脚本在上面挡掉了
    except OSError as e:
        return {"error": "打开失败：%s" % e}
    d = _info(p) or {"path": _tilde(p)}
    d["opened"] = "在资源管理器里选中了" if reveal else ("在资源管理器里打开了这个文件夹" if is_dir else "用默认程序打开了")
    return d
