"""Vesper 找文件、看文件夹、在访达里打开（只读，不改不删任何文件）。

- find_files：按名字在桌面 / 文稿 / 下载 / 家目录里找。名字比较时忽略大小写、空格、下划线、连字符，
  所以语音识别成「Tab Pilot」也能找到 TabPilot；再加一点模糊匹配兜住识别错一两个字母的情况。
  Spotlight（mdfind）作为补充，范围更大。
- 桌面上被桌面整理工具收起的文件带 macOS 的 hidden 标志，桌面上看不到图标，但文件还在；结果里会标出来。
- open_path：打开文件夹或文稿，或者在访达里选中它。不运行应用、脚本和安装包（应用用 screen_open）。
- 只在家目录里活动，跳过 ~/Library、隐藏目录、node_modules 这类。
只用标准库，兼容 macOS 自带 python3 (3.9+)。
"""
import difflib
import os
import re
import stat
import subprocess
import time

HOME = os.path.expanduser("~")
PLACES = {
    "desktop": os.path.join(HOME, "Desktop"),
    "documents": os.path.join(HOME, "Documents"),
    "downloads": os.path.join(HOME, "Downloads"),
    "home": HOME,
}
ALIASES = {"桌面": "desktop", "文稿": "documents", "文档": "documents", "下载": "downloads", "家目录": "home",
           "主目录": "home", "desk": "desktop", "desktop": "desktop", "documents": "documents",
           "downloads": "downloads", "home": "home"}
DEPTH = {"desktop": 3, "documents": 4, "downloads": 2, "home": 2}
SKIP_DIRS = {"Library", "node_modules", ".git", ".venv", "venv", "__pycache__", ".Trash", "Pods", "DerivedData",
             "build", "dist", ".build"}
NO_RUN = {".app", ".command", ".sh", ".zsh", ".bash", ".tool", ".terminal", ".workflow", ".pkg", ".mpkg", ".dmg",
          ".scpt", ".applescript", ".scptd", ".py", ".rb", ".pl", ".jar", ".exe", ".bin", ".run", ".action",
          ".prefPane", ".kext", ".mobileconfig", ".webloc", ".inetloc", ".fileloc", ".url"}
PERM_HINT = ("Deck 没有读这个文件夹的权限。请打开 系统设置 → 隐私与安全性 → 文件和文件夹，"
             "找到 python3（或「完全磁盘访问权限」里加上 python3），把「桌面文件夹」打开。")
UF_HIDDEN = getattr(stat, "UF_HIDDEN", 0x8000)


def _norm(s):
    return re.sub(r"[\s_\-.·]+", "", (s or "").lower())


def _tilde(p):
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + os.sep) else p


def _inside_home(p):
    # 按路径本身判断（不跟随软链接）：桌面上指向别处的软链接文件夹也能打开，但 ../ 跳不出家目录
    p = os.path.normpath(os.path.abspath(p))
    home = os.path.normpath(HOME)
    lib = os.path.join(home, "Library")
    return (p == home or p.startswith(home + os.sep)) and not (p == lib or p.startswith(lib + os.sep))


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


def _info(path, st=None):
    try:
        st = st or os.lstat(path)
    except OSError:
        return None
    is_dir = stat.S_ISDIR(st.st_mode)
    link = stat.S_ISLNK(st.st_mode)
    if link:
        try:
            is_dir = os.path.isdir(path)
        except OSError:
            pass
    d = {"path": _tilde(path), "name": os.path.basename(path), "kind": "文件夹" if is_dir else "文件",
         "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}
    if not is_dir:
        d["size"] = _human(st.st_size)
    if getattr(st, "st_flags", 0) & UF_HIDDEN:
        d["hidden"] = "带隐藏标志（多半是被桌面整理工具收起了）：桌面上看不到图标，但文件还在这里"
    if link:
        d["link"] = True
    return d


def _human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%d %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0
    return str(n)


def _score(query, name):
    q, n = _norm(query), _norm(os.path.splitext(name)[0]) or _norm(name)
    if not q:
        return 0.0
    full = _norm(name)
    if q == n or q == full:
        return 1.0
    if q in full:
        return 0.9 if full.startswith(q) else 0.8
    if len(q) >= 4:
        r = difflib.SequenceMatcher(None, q, n).ratio()
        if r >= 0.75:
            return 0.5 + r * 0.25
    return 0.0


def _walk(root, depth, query, hits, budget):
    stack = [(root, 0)]
    while stack:
        d, lv = stack.pop()
        try:
            it = os.scandir(d)
        except PermissionError:
            if d == root:
                raise
            continue
        except OSError:
            continue
        with it:
            for e in it:
                budget[0] -= 1
                if budget[0] <= 0 or time.monotonic() > budget[1]:
                    return
                s = _score(query, e.name)
                if s:
                    hits.append((s, e.path))
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                if is_dir and lv + 1 < depth and not e.name.startswith(".") and e.name not in SKIP_DIRS \
                        and not e.name.endswith((".app", ".photoslibrary", ".bundle", ".framework")):
                    stack.append((e.path, lv + 1))


def _mdfind(query, limit=40):
    q = re.sub(r"[\"'\\]", "", query).strip()
    if len(q) < 2:
        return []
    try:
        out = subprocess.run(["mdfind", "-onlyin", HOME, "-name", q], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001  Spotlight 关了或超时：只用目录扫描的结果
        return []
    return [p for p in out.splitlines()[:limit * 3] if p and _inside_home(p)][:limit]


def find_files(name, where=None, limit=12):
    name = (name or "").strip()
    if not name:
        return {"error": "要找的名字是空的"}
    keys = []
    if where:
        root = _resolve(where)
        if not root or not _inside_home(root) or not os.path.isdir(root):
            return {"error": "找不到这个位置：%s" % where}
        keys = [(root, 4)]
    else:
        keys = [(PLACES[k], DEPTH[k]) for k in ("desktop", "documents", "downloads", "home")]
    hits, budget, denied = [], [30000, time.monotonic() + 2.5], []
    for root, depth in keys:
        try:
            _walk(root, depth, name, hits, budget)
        except PermissionError:
            denied.append(_tilde(root))
    for p in _mdfind(name):
        s = _score(name, os.path.basename(p))
        if s:
            hits.append((s - 0.05, p))  # 同分时目录扫描（桌面、文稿）优先
    best = {}
    for s, p in hits:
        if s > best.get(p, 0):
            best[p] = s
    order = sorted(best.items(), key=lambda kv: (-kv[1], kv[0].count(os.sep), kv[0]))
    results = [x for x in (_info(p) for p, _ in order[:limit]) if x]
    out = {"query": name, "results": results}
    if where:
        out["where"] = _tilde(_resolve(where))
    if denied:
        out["no_permission"] = denied
        out["hint"] = PERM_HINT
    if not results and not denied:
        out["note"] = "没找到。可以换个说法（名字的一部分也行），或者问他放在哪个文件夹。"
    return out


def list_folder(path, limit=80):
    p = _resolve(path)
    if not p or not _inside_home(p):
        return {"error": "只能看家目录里的文件夹（不含资源库 Library）"}
    if not os.path.isdir(p):
        return {"error": "不是文件夹或不存在：%s" % _tilde(p or path)}
    try:
        entries = sorted(os.scandir(p), key=lambda e: e.name.lower())
    except PermissionError:
        return {"error": PERM_HINT, "path": _tilde(p)}
    items = []
    for e in entries:
        if e.name.startswith(".") or e.name == "Icon\r":
            continue
        d = _info(e.path)
        if d:
            items.append(d)
    hidden = sum(1 for d in items if d.get("hidden"))
    return {"path": _tilde(p), "count": len(items), "hidden_count": hidden, "items": items[:limit],
            "more": max(0, len(items) - limit)}


def open_path(path, reveal=False):
    p = _resolve(path)
    if not p or not _inside_home(p):
        return {"error": "只能打开家目录里的东西（不含资源库 Library）"}
    if not os.path.exists(p):
        return {"error": "不存在：%s（先用 find_files 找到准确路径）" % _tilde(p)}
    is_dir = os.path.isdir(p)
    ext = os.path.splitext(p)[1].lower()
    if not reveal and not is_dir and (ext in NO_RUN or os.access(p, os.X_OK)):
        return {"error": "这是应用、脚本或安装包，不直接运行。可以在访达里选中它（reveal=true），应用用 screen_open。"}
    if not reveal and is_dir and ext in {".app", ".pkg", ".mpkg", ".workflow", ".action", ".prefpane"}:
        return {"error": "这是应用或安装包，不直接打开。应用用 screen_open；也可以在访达里选中它（reveal=true）。"}
    cmd = ["open", "-R", "--", p] if reveal else ["open", "--", p]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except Exception as e:  # noqa: BLE001
        return {"error": "打开失败：%s" % e}
    if r.returncode != 0:
        return {"error": "打开失败：%s" % (r.stderr.strip()[:200] or r.returncode)}
    d = _info(p) or {"path": _tilde(p)}
    d["opened"] = "在访达里选中了" if reveal else ("在访达里打开了这个文件夹" if is_dir else "用默认应用打开了")
    return d
