"""磁盘「谁在占空间」（：点磁盘圆环，看最近新增了什么、在哪、谁加的、多大；想抓到是谁在疯狂往硬盘里塞东西）。

三件事，全部只读、只在本机，不读文件内容，不发给 DeepSeek，也不联网：
- 谁在变大：每小时用 du 量一次家目录（深 3 层，含隐藏文件夹和资源库）、/Applications、用户临时目录，
  存下 ≥50MB 的目录大小；和 1 小时 / 24 小时 / 7 天前比，列出涨得最多的目录，并按路径认出是哪个应用的。
  du 用 taskpolicy -b 跑（后台优先级，磁盘读写会被系统让路），不碰 ~/Library/Containers 这类别的应用的沙盒，
  除非管家本来就有完全磁盘访问权限（否则 macOS 会弹「想访问其他 App 的数据」）。
  做法参考 ncdu / dua 的「按目录汇总」+ 定时快照对比（类似 DaisyDisk 的「对比」功能）。
- 最近新增：Spotlight 的 kMDItemDateAdded（放进文件夹的时间，访达「添加日期」那一列就是它）。
  一次克隆/解压出来的一堆文件收拢成最外层那一个文件夹。来源按这个顺序判断：
  截图标记 → 下载隔离属性 com.apple.quarantine 里记的是哪个应用下的 + kMDItemWhereFroms 的网站 → git 仓库 →
  kMDItemCreator（哪个应用生成的）→ 所在文件夹。
- 谁在写硬盘：proc_pid_rusage 的 ri_diskio_byteswritten（活动监视器「写入字节」同一个数），每 30 秒采一次，
  按 .app 归到应用上。只看得到自己用户的进程。
外置硬盘：/Volumes 下本地挂载、不是磁盘映像/网络盘/时间机器的卷，插上自动认，拔掉自动消失。
只用标准库，兼容 macOS 自带 python3 (3.9+)。
"""
import ctypes
import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import threading
import time
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "logs", "disk-growth.json")
SNAP_EVERY = 3600          # 内置盘每小时量一次
EXT_EVERY = 3 * 3600       # 外置盘 3 小时一次（大、慢）
KEEP_MIN = 50 * 1024 ** 2  # 快照里只存 ≥50MB 的目录
SHOW_MIN = 50 * 1024 ** 2  # 涨了 ≥50MB 才列出来
RECENT_DAYS = 7
_TMP = (os.environ.get("TMPDIR") or tempfile.gettempdir()).rstrip("/")
VAR_USER = os.path.dirname(_TMP) if _TMP.startswith(("/var/folders/", "/private/var/folders/")) else None

_lock = threading.Lock()
_state = None
_recent_cache = {}   # vol -> (时间, 结果)
_shown = {}          # 最近一次返回给页面的路径（「在访达中显示」只认这些）
_shown_lock = threading.Lock()


# ---------------------------------------------------------------- 工具

def _tilde(p):
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def _load():
    global _state
    if _state is None:
        try:
            with open(STATE, encoding="utf-8") as f:
                _state = json.load(f)
        except (OSError, ValueError):
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
    """有没有完全磁盘访问权限：读 ~/Library/Safari 不会弹窗，没权限只是报错。"""
    try:
        os.listdir(os.path.join(HOME, "Library", "Safari"))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 硬盘列表

_diskinfo = {}  # 设备 -> diskutil 信息（每个设备只问一次）


def _mounts():
    try:
        out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in out.splitlines():
        m = re.match(r"^(\S+) on (.+) \(([^)]*)\)$", line)
        if m:
            rows.append((m.group(1), m.group(2), [x.strip() for x in m.group(3).split(",")]))
    return rows


def _dinfo(dev):
    if dev not in _diskinfo:
        try:
            out = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", dev], capture_output=True, timeout=8).stdout
            _diskinfo[dev] = plistlib.loads(out) if out else {}
        except Exception:  # noqa: BLE001
            _diskinfo[dev] = {}
    return _diskinfo[dev]


def _space(path):
    v = os.statvfs(path)
    total, free = v.f_blocks * v.f_frsize, v.f_bavail * v.f_frsize
    return total, free


_vol_cache = [0, None]


def volumes(max_age=10):
    """[{id, name, mount, total, free, pct, ext}]：第一个永远是 Mac 内置盘。"""
    if _vol_cache[1] is not None and time.time() - _vol_cache[0] < max_age:
        return _vol_cache[1]
    vols = []
    for p in ("/System/Volumes/Data", "/"):
        try:
            t, f = _space(p)
            vols.append({"id": "mac", "name": "Macintosh HD", "mount": "/", "total": t, "free": f,
                         "pct": round(100.0 * (t - f) / t, 1) if t else None, "ext": False})
            break
        except OSError:
            continue
    try:
        root_dev = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        root_dev = os.stat("/").st_dev
    for dev, mnt, flags in _mounts():
        if not mnt.startswith("/Volumes/") or not dev.startswith("/dev/disk") or "nobrowse" in flags:
            continue
        name = os.path.basename(mnt)
        if name.startswith(".") or "timemachine" in mnt.lower() or "Backups of" in name:
            continue
        try:
            if os.stat(mnt).st_dev == root_dev:
                continue
            t, f = _space(mnt)
        except OSError:
            continue
        if t < 1024 ** 3:  # 小于 1GB 的多半是安装器、恢复分区
            continue
        info = _dinfo(dev)
        if info.get("Internal") and not info.get("RemovableMedia") and not info.get("Ejectable"):
            continue
        if (info.get("BusProtocol") or "") == "Disk Image" or info.get("VirtualOrPhysical") == "Virtual":
            continue
        if any(r in (info.get("FilesystemType") or "") for r in ("smbfs", "nfs", "afp", "webdav")):
            continue
        vid = "v" + re.sub(r"[^0-9A-Za-z]", "", (info.get("VolumeUUID") or info.get("DiskUUID") or dev))[-12:]
        vols.append({"id": vid, "name": info.get("VolumeName") or name, "mount": mnt, "total": t, "free": f,
                     "pct": round(100.0 * (t - f) / t, 1) if t else None, "ext": True,
                     "fs": info.get("FilesystemUserVisibleName") or info.get("FilesystemType") or ""})
    _vol_cache[:] = [time.time(), vols]
    return vols


def _vol(vid):
    for v in volumes():
        if v["id"] == vid:
            return v
    return None


# ---------------------------------------------------------------- 谁的目录

KNOWN = [  # (路径前缀（~ 开头或绝对路径）, 应用名, 说明)；越具体的放越前面
    ("~/.codex", "Codex", "Codex 的数据和临时文件"),
    ("~/.claude", "Claude Code", "Claude Code 的会话和设置"),
    ("~/Library/Application Support/Claude", "Claude", "Claude 桌面版"),
    ("~/Library/Caches/claude-cli-nodejs", "Claude Code", "缓存"),
    ("~/Documents/Codex", "Codex", "Codex 的工作区"),
    ("~/.cache/uv", "uv", "Python 包缓存 · 可以清"),
    ("~/.cache/huggingface", "Hugging Face", "模型缓存"),
    ("~/.cache/pip", "pip", "Python 包缓存 · 可以清"),
    ("~/.cache", "命令行工具", "缓存"),
    ("~/.npm", "npm", "Node 包缓存 · 可以清"),
    ("~/Library/pnpm", "pnpm", "Node 包仓库"),
    ("~/.pnpm-store", "pnpm", "Node 包仓库"),
    ("~/.bun", "Bun", ""), ("~/.cargo", "Rust", ""), ("~/.rustup", "Rust", "工具链"),
    ("~/.gradle", "Gradle", "缓存"), ("~/.m2", "Maven", "缓存"), ("~/.docker", "Docker", ""),
    ("~/.ollama", "Ollama", "本地模型"), ("~/.lmstudio", "LM Studio", "本地模型"),
    ("~/.cursor", "Cursor", ""), ("~/.vscode", "VS Code", "扩展"), ("~/.dsh", "DeepSeek Harness", ""),
    ("~/.Trash", "废纸篓", "清空废纸篓才会腾出空间"),
    ("~/Library/Developer/Xcode/DerivedData", "Xcode", "编译缓存 · 可以清"),
    ("~/Library/Developer/CoreSimulator", "Xcode", "模拟器"),
    ("~/Library/Developer", "Xcode", ""),
    ("~/Library/Caches/Google", "Chrome", "缓存 · 可以清"),
    ("~/Library/Caches/com.google.Chrome", "Chrome", "缓存 · 可以清"),
    ("~/Library/Application Support/Google/Chrome", "Chrome", "浏览数据"),
    ("~/Library/Containers/com.tencent.xinWeChat", "微信", "聊天文件"),
    ("~/Library/Containers/com.lemon.lvpro", "剪映", "草稿和缓存"),
    ("~/Movies/JianyingPro", "剪映", "草稿"),
    ("~/Library/Mobile Documents", "iCloud 云盘", ""),
    ("~/Library/CloudStorage", "网盘", ""),
    ("~/Library/Messages", "信息", "聊天附件"),
    ("~/Library/Mail", "邮件", ""),
    ("~/Pictures/Photos Library.photoslibrary", "照片", "图库"),
    ("~/Library/Group Containers", "", "多个应用共用的数据"),
    ("~/Library/Caches", "", "缓存"),
    ("~/Library/Logs", "", "日志"),
    ("~/Library/Application Support", "", "应用数据"),
    ("~/Library/Containers", "", "应用沙盒"),
    ("~/Downloads", "", "下载"),
    ("~/Library", "", "资源库"),
    ("~/Desktop", "", "桌面"),
    ("~/Documents", "", "文稿"),
    ("/Applications", "", "应用"),
]
_KNOWN = [(os.path.expanduser(p) if p.startswith("~") else p, a, d) for p, a, d in KNOWN]
_bundle_names = {}


def _bundle_app(bid):
    """com.google.Chrome → Google Chrome（用 Spotlight 找应用，只查一次）。"""
    if bid in _bundle_names:
        return _bundle_names[bid]
    name = ""
    if re.match(r"^[A-Za-z0-9][\w.-]+$", bid) and bid.count(".") >= 2:
        try:
            out = subprocess.run(["/usr/bin/mdfind", "kMDItemCFBundleIdentifier == '%s'" % bid],
                                 capture_output=True, text=True, timeout=4).stdout.split("\n")
            app = next((p for p in out if p.endswith(".app")), "")
            name = os.path.basename(app)[:-4] if app else ""
        except Exception:  # noqa: BLE001
            name = ""
    _bundle_names[bid] = name
    return name


def owner(path):
    """路径 → (应用名, 说明)。"""
    for pre, app, desc in _KNOWN:
        if path == pre or path.startswith(pre + "/"):
            rest = path[len(pre) + 1:].split("/")[0] if path != pre else ""
            if not app and rest:
                if pre == "/Applications" and rest.endswith(".app"):
                    return rest[:-4], "应用本身"
                guess = _bundle_app(rest) if "." in rest and not rest.endswith(".app") else ""
                return guess or rest, desc
            return app, desc
    if VAR_USER and (path == VAR_USER or path.startswith(VAR_USER + "/")):
        return "系统临时文件", "/var/folders 里你自己的那份，重启或应用退出时多半会清"
    rel = path[len(HOME) + 1:] if path.startswith(HOME + "/") else path
    first = rel.split("/")[0]
    if first.startswith("."):
        return first[1:] or first, "隐藏文件夹"
    return "", ""


# ---------------------------------------------------------------- 谁在变大

def _du(root, depth, extra=()):
    cmd = ["/usr/sbin/taskpolicy", "-b", "/usr/bin/du", "-k", "-x", "-d", str(depth)] + list(extra) + [root]
    if not os.path.exists("/usr/sbin/taskpolicy"):
        cmd = cmd[2:]
    sizes = {}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception:  # noqa: BLE001
        return sizes
    for line in p.stdout.splitlines():
        kb, _, path = line.partition("\t")
        try:
            b = int(kb) * 1024
        except ValueError:
            continue
        if b >= KEEP_MIN or path == root:
            sizes[path] = b
    return sizes


def snapshot(vid="mac"):
    """量一次（几分钟，在后台线程里跑）。"""
    v = _vol(vid)
    if not v:
        return None
    sizes = {}
    if vid == "mac":
        # 没有完全磁盘访问权限时跳过别的应用的沙盒和照片图库，免得 macOS 弹隐私窗口
        extra = [] if has_fda() else ["-I", "Containers", "-I", "Group Containers", "-I", "*.photoslibrary"]
        sizes.update(_du(HOME, 3, extra))
        sizes.update(_du("/Applications", 1))
        if VAR_USER and os.path.isdir(VAR_USER):
            sizes.update(_du(VAR_USER, 2))
    else:
        sizes.update(_du(v["mount"], 2, ["-I", ".Spotlight-V100", "-I", ".fseventsd"]))
    snap = {"at": time.time(), "free": v["free"], "total": v["total"],
            "sizes": {(_tilde(k) if vid == "mac" else k[len(v["mount"]):] or "/"): s for k, s in sizes.items()}}
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
        return os.path.expanduser(key) if key.startswith("~") else key
    v = _vol(vid)
    return (v["mount"] if v else "") + (key if key != "/" else "")


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
    roots = {"~", "/Applications", "/"} | ({_tilde(VAR_USER)} if VAR_USER else set())
    cand = {k: a[k] - b.get(k, 0) for k in a if k not in roots and a[k] - b.get(k, 0) >= SHOW_MIN}
    # 从最深的往上看：子目录已经列出来的那部分不再算到父目录头上；父目录剩下的（散在很多小文件夹里）够多才列一行「其它零散」
    acc, kept, extra = {}, {}, set()
    order = sorted(cand, key=lambda x: -x.count("/"))
    for k in order:
        inner = [c for c in acc if c.startswith(k + "/")]
        near = [c for c in inner if not any(o != c and c.startswith(o + "/") for o in inner)]
        rest = cand[k] - sum(acc[c] for c in near)
        if rest >= SHOW_MIN and rest >= cand[k] * 0.3:
            kept[k] = rest
            if near:
                extra.add(k)
        acc[k] = cand[k]
    rows = []
    for k, d in sorted(kept.items(), key=lambda x: -x[1])[:n]:
        full = _abs(vid, k)
        app, desc = owner(full) if vid == "mac" else ("", "外置盘文件夹")
        if k in extra:
            desc = "其它零散的（上面单列的不算在内）"
        rows.append({"path": k, "full": full, "name": os.path.basename(k.rstrip("/")) or k, "app": app, "desc": desc,
                     "size": a[k], "delta": d, "new": k not in b})
    out["rows"] = rows
    _remember(vid, [r["full"] for r in rows])
    return out


# ---------------------------------------------------------------- 最近新增

SKIP_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "DerivedData", ".build", "Pods"}


def _qagent(path):
    """下载隔离属性：'0083;65a1b2c3;Google Chrome;UUID' → 'Google Chrome'。"""
    try:
        libc = _libc()
        buf = ctypes.create_string_buffer(512)
        n = libc.getxattr(path.encode(), b"com.apple.quarantine", buf, 512, 0, 1)  # XATTR_NOFOLLOW
        if n <= 0:
            return None
        parts = buf.raw[:n].decode("utf-8", "replace").split(";")
        return parts[2] if len(parts) > 2 and parts[2] else ""
    except Exception:  # noqa: BLE001
        return None


_LIBC = []


def _libc():
    if not _LIBC:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        lib.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        lib.getxattr.restype = ctypes.c_ssize_t
        _LIBC.append(lib)
    return _LIBC[0]


MD_NAMES = ["kMDItemWhereFroms", "kMDItemCreator", "kMDItemIsScreenCapture", "kMDItemDateAdded", "kMDItemContentType"]


def _mdls(path):
    try:
        cmd = ["/usr/bin/mdls", "-plist", "-"]
        for n in MD_NAMES:
            cmd += ["-name", n]
        out = subprocess.run(cmd + ["--", path], capture_output=True, timeout=5).stdout
        return plistlib.loads(out) if out else {}
    except Exception:  # noqa: BLE001
        return {}


AGENTS = {"Google Chrome": "Chrome", "Safari": "Safari", "Microsoft Edge": "Edge", "Firefox": "Firefox", "Arc": "Arc",
          "sharingd": "隔空投送", "WeChat": "微信", "微信": "微信", "Feishu": "飞书", "Lark": "飞书", "飞书": "飞书",
          "DingTalk": "钉钉", "钉钉": "钉钉", "QQ": "QQ", "Telegram": "Telegram", "Mail": "邮件", "邮件": "邮件",
          "Slack": "Slack", "Discord": "Discord", "LocalSend": "LocalSend", "Claude": "Claude", "Codex": "Codex", "ChatGPT": "ChatGPT"}
CHAT = {"微信", "飞书", "钉钉", "QQ", "Telegram", "Slack", "Discord"}


def _host(urls):
    for u in urls or []:
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            h = urllib.parse.urlsplit(u).hostname or ""
            return h[4:] if h.startswith("www.") else h
    return ""


def source(path, is_dir, md, qa):
    """→ (徽章文字, 颜色类, 一句说明)。颜色类：w 下载、g 代码/聊天、x 截图/系统、空 = 应用。"""
    name = os.path.basename(path)
    froms = md.get("kMDItemWhereFroms") or []
    if md.get("kMDItemIsScreenCapture"):
        return "截图", "x", "macOS 截图"
    if re.match(r"^(录屏|Screen Recording) ", name):
        return "录屏", "x", "macOS 录屏"
    if qa is not None:
        app = AGENTS.get(qa) or next((v for k, v in AGENTS.items() if len(k) > 3 and k.lower() in (qa or "").lower()), "")
        if not app and qa:
            # com.apple.Safari.SandboxBroker.xpc 这种包名：取有意义的那一段
            segs = [x for x in qa.split(".") if x and x.lower() not in ("com", "apple", "xpc", "app", "helper", "sandboxbroker")]
            app = segs[0] if "." in qa and segs else qa
        host = _host(froms)
        if app == "LocalSend":
            return "LocalSend", "", "用 LocalSend 传来的"
        if app == "隔空投送":
            who = next((f for f in froms if isinstance(f, str) and not f.startswith("http")), "")
            return "隔空投送", "", ("从 %s 传来" % who) if who else "隔空投送收到的"
        if app in CHAT:
            return app, "g", "%s收到的文件" % app
        if app == "邮件":
            return "邮件附件", "", "邮件里存下来的附件"
        if host:
            return "%s 下载" % (app or "浏览器"), "w", "从 %s 下载" % host
        if app:
            return "%s 下载" % app if app in ("Chrome", "Safari", "Edge", "Firefox", "Arc") else app, "w", "由 %s 下载或保存" % app
    if froms and _host(froms):
        return "下载", "w", "从 %s 下载" % _host(froms)
    if is_dir and os.path.isdir(os.path.join(path, ".git")):
        return "git 克隆", "g", "代码仓库"
    creator = md.get("kMDItemCreator")
    if isinstance(creator, str) and creator.strip():
        c = creator.strip()[:30]
        return c, "", "由 %s 生成" % c
    app, desc = owner(path)
    if app:
        return app, "", desc or "在 %s 的文件夹里" % app
    parent = os.path.basename(os.path.dirname(path))
    return "不确定", "x", "在「%s」文件夹里" % parent if parent else "看不出来源"


def _sizeof(st):
    return st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size


_recent_busy = set()


def recent(vid="mac"):
    """Spotlight 一次要好几秒（整个家目录 7 天有几十万个文件，大多是资源库里的缓存），所以放到后台线程里查：
    2 分钟内的结果直接给；过期了先给旧的、后台重查（loading=True，页面过 2 秒再来拿）。"""
    hit = _recent_cache.get(vid)
    fresh = hit and time.time() - hit[0] < 120
    if not fresh and vid not in _recent_busy:
        _recent_busy.add(vid)

        def job():
            try:
                res = _recent(vid)
                _recent_cache[vid] = (time.time(), res)
            except Exception as e:  # noqa: BLE001
                _recent_cache[vid] = (time.time(), {"error": "Spotlight 没查成：%s" % type(e).__name__, "rows": []})
            finally:
                _recent_busy.discard(vid)
        threading.Thread(target=job, name="disk-recent", daemon=True).start()
    if hit:
        return dict(hit[1], loading=vid in _recent_busy)
    return {"loading": True, "rows": []}


def _roots(vid, v):
    """只查用户自己的文件夹：家目录下不带点、不是资源库的那些，加上 /Applications。资源库和隐藏文件夹里的缓存看「谁在变大」。"""
    if vid != "mac":
        return [v["mount"]]
    out = ["/Applications"]
    try:
        for e in os.scandir(HOME):
            if not e.name.startswith(".") and e.name not in ("Library",) and e.is_dir(follow_symlinks=False):
                out.append(e.path)
    except OSError:
        pass
    return out


def _mdfind(root, q):
    try:
        p = subprocess.run(["/usr/bin/mdfind", "-0", "-onlyin", root, q], capture_output=True, timeout=40)
    except Exception:  # noqa: BLE001
        return []
    return [raw.decode("utf-8", "surrogateescape") for raw in p.stdout.split(b"\0")[:200000] if raw]


_git_cache = {}


def _git_root(p, stop):
    """p 所在的 git 仓库根目录（往上找 .git，结果按目录缓存）；不在仓库里返回 ""。"""
    d = os.path.dirname(p)
    seen = []
    root = ""
    while d and d not in ("/", HOME, stop):
        if d in _git_cache:
            root = _git_cache[d]
            break
        seen.append(d)
        if os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(os.path.join(d, ".git")):
            root = d
            break
        d = os.path.dirname(d)
    for x in seen:
        _git_cache[x] = root
    return root


def _recent(vid="mac", days=RECENT_DAYS, n=60):
    """最近 days 天新放进来的文件，一次克隆/解压的收拢成最外层文件夹。"""
    v = _vol(vid)
    if not v:
        return {"error": "这块硬盘已经拔掉了", "rows": []}
    q = "kMDItemDateAdded >= $time.now(-%d)" % int(days * 86400)
    t0 = time.time()
    _git_cache.clear()
    paths = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for lst in ex.map(lambda r: _mdfind(r, q), _roots(vid, v)):
            paths.extend(lst)
    if not paths and vid != "mac":
        return {"rows": [], "note": "这块硬盘没开 Spotlight 索引，看不到最近新增。可以在 系统设置 → Spotlight 里把它从「隐私」中移除。"}
    pset = set(paths)
    tops = {}  # 最外层路径 -> [大小, 文件数, 最新时间]
    repos = set()
    for p in paths:
        rel = p[len(HOME) + 1:] if p.startswith(HOME + "/") else p
        parts = rel.split("/")
        if any(x.startswith(".") for x in parts) or SKIP_PARTS.intersection(parts):
            continue
        # 代码仓库里的新文件（拉代码、编译、AI 改代码）收成「这个仓库」一行，不然整页都是项目里的零碎文件
        top = _git_root(p, v["mount"]) or None
        if top:
            repos.add(top)
        else:
            top, cur = p, os.path.dirname(p)
            while cur and cur not in ("/", HOME, "/Applications", v["mount"]):
                if cur in pset:
                    top = cur
                cur = os.path.dirname(cur)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        rec = tops.setdefault(top, [0, 0, 0.0])
        if not stat.S_ISDIR(st.st_mode):
            rec[0] += _sizeof(st)
            rec[1] += 1
        rec[2] = max(rec[2], st.st_ctime)
    # .app 是一个整体：它本身在列表里时，里面的文件 Spotlight 不一定都列出来，用 du 补一下
    items = sorted(tops.items(), key=lambda x: -x[1][2])
    by_time = items[:n]
    by_size = sorted(items, key=lambda x: -x[1][0])[:n]
    pick = dict(by_time + by_size)

    def build(kv):
        p, (size, cnt, t) = kv
        try:
            st = os.lstat(p)
        except OSError:
            return None
        is_dir = stat.S_ISDIR(st.st_mode)
        if is_dir and (p.endswith(".app") or cnt == 0):
            try:
                out = subprocess.run(["/usr/bin/du", "-sk", p], capture_output=True, text=True, timeout=5).stdout
                size = int(out.split()[0]) * 1024
            except Exception:  # noqa: BLE001
                pass
        elif not is_dir:
            size = _sizeof(st)
        md = _mdls(p)
        qa = _qagent(p)
        if qa is None and is_dir and p not in repos and not p.endswith(".app"):
            # 文件夹本身没有隔离属性时（比如解压出来的）：看里面最多 5 个文件，都是同一个应用下载的才算它的
            agents = []
            try:
                for e in os.scandir(p):
                    if e.is_file(follow_symlinks=False) and not e.name.startswith("."):
                        agents.append(_qagent(e.path))
                        if len(agents) >= 5:
                            break
            except OSError:
                pass
            if agents and agents[0] is not None and all(a == agents[0] for a in agents):
                qa = agents[0]
        added = md.get("kMDItemDateAdded")
        added = added.timestamp() if hasattr(added, "timestamp") else t
        if p in repos:
            cloned = (added or 0) >= time.time() - days * 86400 and p in pset
            badge, cls = ("git 克隆", "g") if cloned else ("代码仓库", "g")
            desc = ("新克隆的仓库" if cloned else "仓库里新增或重建了 %d 个文件" % cnt)
            return {"path": _tilde(os.path.dirname(p)) if vid == "mac" else os.path.dirname(p), "full": p,
                    "name": os.path.basename(p), "dir": True, "size": size, "added": t if not cloned else added,
                    "src": badge, "cls": cls, "desc": desc}
        badge, cls, desc = source(p, is_dir, md, qa)
        if is_dir and cnt > 1 and badge not in ("git 克隆",):
            desc = desc + " · %d 个文件" % cnt
        elif is_dir and cnt > 1:
            desc = "文件夹 · %d 个文件" % cnt
        return {"path": _tilde(os.path.dirname(p)) if vid == "mac" else os.path.dirname(p), "full": p,
                "name": os.path.basename(p), "dir": is_dir, "size": size, "added": added,
                "src": badge, "cls": cls, "desc": desc}

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = [r for r in ex.map(build, pick.items()) if r]
    res = {"rows": rows, "days": days, "found": len(paths), "took": round(time.time() - t0, 1), "at": time.time()}
    _remember(vid, [r["full"] for r in rows])
    return res


# ---------------------------------------------------------------- 谁在写硬盘

class _RU(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size", "resident_size",
        "phys_footprint", "proc_start_abstime", "proc_exit_abstime", "child_user_time", "child_system_time",
        "child_pkg_idle_wkups", "child_interrupt_wkups", "child_pageins", "child_elapsed_abstime",
        "diskio_bytesread", "diskio_byteswritten")]


class Writers:
    """每 30 秒记一次每个进程累计写了多少字节，留 1 小时。"""
    EVERY = 30

    def __init__(self):
        self.samples = deque(maxlen=int(3600 / self.EVERY) + 2)
        self.names = {}
        try:
            self.lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        except OSError:
            self.lib = None

    def _app(self, pid):
        if pid in self.names:
            return self.names[pid]
        buf = ctypes.create_string_buffer(4096)
        n = self.lib.proc_pidpath(pid, buf, 4096)
        path = buf.value.decode("utf-8", "replace") if n > 0 else ""
        m = re.search(r"^(.*?/([^/]+)\.app)/", path)
        if m:
            name = m.group(2)
        elif path:
            name = os.path.basename(path)
        else:
            name = "进程 %d" % pid
        sysd = path.startswith(("/System/", "/usr/", "/sbin/", "/bin/", "/Library/Apple/")) and not m
        self.names[pid] = (name, sysd)
        return self.names[pid]

    def sample(self):
        if not self.lib:
            return
        n = self.lib.proc_listallpids(None, 0)
        if n <= 0:
            return
        buf = (ctypes.c_int * (n + 64))()
        n = self.lib.proc_listallpids(buf, ctypes.sizeof(buf))
        cur = {}
        ru = _RU()
        for pid in buf[:max(0, n)]:
            if pid <= 0 or self.lib.proc_pid_rusage(pid, 2, ctypes.byref(ru)) != 0:
                continue
            cur[pid] = ru.diskio_byteswritten
        # pid 会被复用：消失的 pid 把名字也忘掉
        for pid in list(self.names):
            if pid not in cur:
                del self.names[pid]
        self.samples.append((time.time(), cur))

    def top(self, window=600, n=12):
        if not self.samples:
            self.sample()
        samples = list(self.samples)  # 采样线程同时在 append，先拷一份
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
            name, sysd = self._app(pid)
            a = apps.setdefault(name, {"name": name, "sys": sysd, "bytes": 0, "total": 0, "procs": 0})
            a["bytes"] += max(0, d)
            a["total"] += w
            a["procs"] += 1
        rows = sorted(apps.values(), key=lambda a: -a["bytes"])
        rows = [r for r in rows if r["bytes"] > 0][:n]
        span = (now - base[0]) if base and window else None
        return {"rows": rows, "span": span, "since": samples[0][0], "at": now}


WRITERS = Writers()


# ---------------------------------------------------------------- 在访达中显示

def _remember(vid, paths):
    with _shown_lock:
        s = _shown.setdefault(vid, deque(maxlen=400))
        for p in paths:
            s.append(p)


def reveal(path):
    """只认最近返回给页面过的路径，不能用来打开任意东西；用 open -R 只在访达里选中，不运行。"""
    path = str(path or "")
    with _shown_lock:
        ok = any(path in s for s in _shown.values())
    if not ok or not os.path.lexists(path):
        return {"error": "这个文件已经不在了，或者不在列表里"}
    try:
        r = subprocess.run(["/usr/bin/open", "-R", "--", path], capture_output=True, text=True, timeout=8)
    except Exception as e:  # noqa: BLE001
        return {"error": "打开访达失败：%s" % e}
    return {"ok": r.returncode == 0, "error": None if r.returncode == 0 else "打开访达失败"}


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


def start():
    def loop():
        time.sleep(90)  # 等服务器起来、开机那阵忙过去
        last = {}
        while True:
            try:
                WRITERS.sample()
            except Exception:  # noqa: BLE001
                pass
            now = time.time()
            for v in volumes(max_age=0):
                every = SNAP_EVERY if v["id"] == "mac" else EXT_EVERY
                if not last.get(v["id"]):
                    with _lock:
                        lst = _load()["snaps"].get(v["id"]) or []
                    last[v["id"]] = lst[-1]["at"] if lst else 0
                if now - last[v["id"]] >= every:
                    last[v["id"]] = now
                    threading.Thread(target=_safe_snap, args=(v["id"],), name="disk-snap", daemon=True).start()
            time.sleep(Writers.EVERY)
    threading.Thread(target=loop, name="diskwatch", daemon=True).start()


_snapping = set()


def _safe_snap(vid):
    if vid in _snapping:
        return
    _snapping.add(vid)
    try:
        snapshot(vid)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _snapping.discard(vid)
