"""工具箱：卸载应用（连同残留文件）、垃圾清理、文件管理、应用更新。

做法参考了几个开源项目，但代码是这里重写的（它们是 GPL-3.0 或带 Commons Clause，不拷代码）：
- 卸载残留：Pearcleaner（alienator88/Pearcleaner）的搜索位置和「按 bundle id / 应用名匹配」的思路。
- 垃圾分类：Mole（tw93/Mole）的 mo clean / mo purge 分类。Mole 会直接删，这里只移进废纸篓。
- 更新检查：Latest（mangerlahn/Latest）的 Sparkle 更新源 + App Store 检查，再加上 Homebrew。

铁律：
- 绝不直接删除。只移进废纸篓（~/.Trash），记下原位置，可以一键放回。
- 先列清单给人看，点确认后才动。页面只能传「清单里第几项」，不能传任意路径；
  执行前对每个路径再检查一遍（还在、不是软链接、在允许的位置里、不在保护名单里）。
- 不清空废纸篓。清空由用户自己在访达里做。
- 不提权：需要管理员权限的东西只列出来，让他在访达里自己处理。

只用标准库，兼容 macOS 自带 python3 (3.9+)。
"""
import calendar
import fnmatch
import glob
import hashlib
import json
import os
import plistlib
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid

import cleaner

HOME = os.path.expanduser("~")
TRASH = os.path.join(HOME, ".Trash")
LIB = os.path.join(HOME, "Library")
TOOLS_LOG = os.path.join(LIB, "Logs", "deck-tools.log")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(LIB, "Application Support", "Deck", "tools-state.json")
BREW = next((b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(b)), None)
DAY = 86400

APP_DIRS = ["/Applications", os.path.join(HOME, "Applications")]
# 管家自己和它依赖的东西不让卸
SELF_PROTECT = {"Mac 管家.app", "Fusion Workbench.app", "Deck.app", "DeckVoice.app"}

# 卸载时找残留的位置（参考 Pearcleaner 的 Locations.swift，只取用户目录里常见的那些）
USER_LOCATIONS = [
    "Application Support", "Application Scripts", "Caches", "Containers", "Group Containers", "HTTPStorages",
    "Preferences", "Preferences/ByHost", "Saved Application State", "Logs", "LaunchAgents", "WebKit", "Cookies",
    "Services", "Internet Plug-Ins", "PreferencePanes", "Application Support/CrashReporter",
]
SYSTEM_LOCATIONS = ["/Library/Application Support", "/Library/Caches", "/Library/LaunchAgents", "/Library/LaunchDaemons",
                    "/Library/Preferences", "/Library/PrivilegedHelperTools", "/Library/Logs"]
# 太常见的名字，单靠名字匹配会误中别的东西
GENERIC_NAMES = {"app", "apps", "code", "data", "helper", "google", "microsoft", "adobe", "apple", "cache", "caches",
                 "logs", "update", "updater", "installer", "launcher", "agent", "service", "client", "desktop", "studio",
                 "player", "editor", "browser", "mail", "music", "notes", "photos", "preview", "terminal", "settings"}
# 名字对不上的知名应用（参考 Pearcleaner Conditions.swift 的 includeForce）
FORCE = {
    "com.microsoft.VSCode": ["~/Library/Application Support/Code", "~/.vscode"],
    "com.google.Chrome": ["~/Library/Application Support/Google/Chrome", "~/Library/Caches/Google/Chrome"],
    "com.tinyspeck.slackmacgap": ["~/Library/Application Support/Slack"],
    "com.todesktop.230313mzl4w4u92": ["~/Library/Application Support/Cursor", "~/.cursor"],
}
# 永远不碰的位置
NEVER = [os.path.join(LIB, n) for n in ("Keychains", "Mail", "Messages", "Photos", "Mobile Documents", "CloudStorage")]


# ---------------------------------------------------------------- 小工具
def _home(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def _norm(s):
    return re.sub(r"[\s\-_.]+", "", (s or "").lower())


def _run(cmd, timeout=20, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def _du(p, timeout=30, strict=False):
    """字节数（du -sk，不跟随软链接）。strict=True 时读不全（没权限）返回 None。"""
    if os.path.islink(p) or not os.path.lexists(p):
        return 0
    if not os.path.isdir(p):
        try:
            return os.lstat(p).st_size
        except OSError:
            return 0
    code, out, _ = _run(["du", "-sk", p], timeout)
    if strict and code != 0:
        return None
    try:
        return int(out.split()[0]) * 1024
    except (IndexError, ValueError):
        return 0


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


def _inside(p, root):
    p, root = os.path.normpath(p), os.path.normpath(root)
    return p == root or p.startswith(root.rstrip("/") + "/")


def _safe_target(p):
    """能不能把 p 移进废纸篓：返回 None 表示可以，否则返回原因。"""
    if not isinstance(p, str) or not os.path.isabs(p) or "\x00" in p:
        return "路径不对"
    p = os.path.normpath(p)
    if not os.path.lexists(p):
        return "已经不在了"
    if os.path.realpath(os.path.dirname(p)) != os.path.dirname(p):
        return "路径里有软链接"
    if p in ("/", HOME, LIB, TRASH, "/Applications", os.path.join(HOME, "Applications")) or _inside(p, TRASH):
        return "受保护的位置"
    if _inside(p, "/System") or _inside(p, "/usr") and not _inside(p, "/usr/local") or _inside(p, "/bin") or _inside(p, "/sbin"):
        return "系统位置"
    if any(_inside(p, n) for n in NEVER):
        return "受保护的位置"
    if os.path.dirname(p) in (HOME, LIB) and os.path.isdir(p) and not os.path.basename(p).startswith("."):
        return "受保护的位置"
    if cleaner._protected(p):
        return "在保护名单里"
    if not os.access(os.path.dirname(p), os.W_OK):
        return "需要管理员权限，请在访达里自己拖到废纸篓"
    return None


def to_trash(p):
    """移进废纸篓（只移动，不删除；不覆盖废纸篓里已有的东西；跨磁盘不复制）。返回废纸篓里的新路径。"""
    os.makedirs(TRASH, exist_ok=True)
    return cleaner.move_to_trash(p, _safe_target)


def _trash_many(items, title):
    """items: [{path, bytes}]。返回 {moved, failed, bytes}。"""
    moved, failed, logs = [], [], [title]
    for it in items:
        src = it["path"]
        try:
            dst = to_trash(src)
            moved.append({"src": src, "dst": dst, "bytes": it.get("bytes", 0)})
            logs.append("移到废纸篓：%s -> %s (%dM)" % (src, dst, round(it.get("bytes", 0) / 1048576)))
        except OSError as e:
            failed.append({"path": _home(src), "why": e.strerror or str(e)})
            logs.append("失败：%s（%s）" % (src, e.strerror or e))
    total = sum(m["bytes"] for m in moved)
    logs.append("完成：移走 %d 项，共 %dM" % (len(moved), round(total / 1048576)))
    _log(logs)
    return {"moved": moved, "failed": failed, "bytes": total}


def human(n):
    return cleaner.human(n)


# ---------------------------------------------------------------- 应用列表
def _plist(app):
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            d = plistlib.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001  坏掉的 plist 很常见
        return {}


def _find_apps():
    out = []
    for d in APP_DIRS:
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            if n.startswith("."):
                continue  # 名字以点开头的是藏起来的系统辅助程序（比如 Karabiner 的驱动管理器），不列出
            if n.endswith(".app"):
                out.append(p)
            elif os.path.isdir(p) and not os.path.islink(p) and not n.startswith("."):
                try:  # 像「/Applications/Adobe Photoshop 2025/xxx.app」这种多一层的
                    out += [os.path.join(p, m) for m in sorted(os.listdir(p)) if m.endswith(".app")]
                except OSError:
                    pass
    return out


def _mdls(paths):
    """一次问 Spotlight 要所有应用的大小和上次使用时间（快）。"""
    res = {}
    if not paths:
        return res
    code, out, _ = _run(["mdls", "-name", "kMDItemPhysicalSize", "-name", "kMDItemLastUsedDate", "-raw", "-nullMarker", "-"]
                        + paths, 30)
    if code != 0:
        return res
    vals = out.split("\x00")
    for i, p in enumerate(paths):
        try:
            used, size = vals[2 * i], vals[2 * i + 1]  # mdls 按属性名字母序输出：LastUsedDate 在前
        except IndexError:
            break
        r = {}
        if size.strip().isdigit():
            r["bytes"] = int(size)
        if used and used != "-":
            try:
                r["last_used"] = calendar.timegm(time.strptime(used.strip()[:19], "%Y-%m-%d %H:%M:%S"))  # mdls 给的是 UTC
            except ValueError:
                pass
        res[p] = r
    return res


_CASKS = {"at": 0, "map": {}}


def brew_casks():
    """已装的 Homebrew cask：{"App.app": {"token", "version", "auto_updates"}}。缓存 10 分钟。"""
    if not BREW:
        return {}
    if time.time() - _CASKS["at"] < 600:
        return _CASKS["map"]
    code, out, _ = _run([BREW, "info", "--cask", "--installed", "--json=v2"], 60,
                        env=dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1"))
    m = {}
    if code == 0:
        try:
            for c in json.loads(out).get("casks", []):
                for art in c.get("artifacts") or []:
                    app = art.get("app") if isinstance(art, dict) else None
                    if not app or not isinstance(app[0], str):
                        continue
                    # ["Foo.app", {"target": "Bar.app"}]：装到 /Applications 后叫 target 的名字
                    tgt = next((x.get("target") for x in app[1:] if isinstance(x, dict) and x.get("target")), None)
                    m[os.path.basename(tgt or app[0])] = {"token": c.get("token"), "version": c.get("installed") or c.get("version"),
                                                          "auto_updates": bool(c.get("auto_updates"))}
        except (ValueError, AttributeError):
            pass
    _CASKS.update(at=time.time(), map=m)
    return m


def _running_apps():
    code, out, _ = _run(["ps", "-axo", "comm="], 5)
    return out if code == 0 else ""


def list_apps():
    paths = _find_apps()
    meta = _mdls(paths)
    casks = brew_casks()
    ps = _running_apps()
    apps = []
    for p in paths:
        info = _plist(p)
        bid = info.get("CFBundleIdentifier") or ""
        name = os.path.basename(p)[:-4]
        # 用 Safari「添加到程序坞」做的网页应用（com.apple.Safari.WebApp.*）是你自己建的，可以删
        system = (bid.startswith("com.apple.") and not bid.startswith("com.apple.Safari.WebApp.")) or _inside(os.path.realpath(p), "/System")
        m = meta.get(p, {})
        apps.append({
            "path": p, "name": name, "bid": bid,
            "version": str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or ""),
            "bytes": m.get("bytes"), "last_used": m.get("last_used"),
            "mas": os.path.exists(os.path.join(p, "Contents", "_MASReceipt", "receipt")),
            "brew": (casks.get(os.path.basename(p)) or {}).get("token"),
            "sparkle": bool(info.get("SUFeedURL")),
            "running": (p + "/Contents/MacOS/") in ps,
            "protected": "系统应用" if system else "Mac 管家自己用的" if os.path.basename(p) in SELF_PROTECT else "",
        })
    apps.sort(key=lambda a: a["name"].lower())
    return apps


# ---------------------------------------------------------------- 卸载：找残留
def _team_id(app):
    code, out, err = _run(["codesign", "-dv", app], 10)
    m = re.search(r"TeamIdentifier=(\w+)", out + err)
    return m.group(1) if m and m.group(1) != "not" else ""


def _strip_ext(n):
    for ext in (".plist", ".savedState", ".binarycookies", ".log", ".sfl2", ".sfl3", ".db"):
        if n.endswith(ext):
            return n[: -len(ext)]
    return n


def _match(child, bid, names, team, other_bids):
    """返回 (理由, 默认勾不勾)，不匹配返回 None。"""
    base = _strip_ext(child)
    low = base.lower()
    b = bid.lower()
    if b:
        if low == b or low.startswith(b + ".") or low.startswith(b + "-"):
            # com.foo.app.helper 也算，但如果它正好是另一个已装应用的 id 就不算
            if low != b and any(low == o or low.startswith(o + ".") for o in other_bids if o.startswith(b + ".")):
                return None
            return ("bundle id 一致", True)
        if team and (low == team.lower() + "." + b or low.startswith(team.lower() + "." + b + ".")
                     or low == team.lower() + ".group." + b or low.startswith(team.lower() + ".group." + b + ".")):
            return ("同一开发者的共享容器", True)
        if low == "group." + b or low.startswith("group." + b + "."):
            return ("应用组容器", True)
    if team and low.startswith(team.lower() + ".") and any(n in _norm(base) for n in names if len(n) >= 4):
        return ("同一开发者的共享容器，名字里有这个应用（可能还有别的应用在用）", False)
    n = _norm(base)
    if n and len(n) >= 4 and n not in GENERIC_NAMES and n in names:
        return ("名字一致", True)  # 太大的会在 uninstall_plan 里改成默认不勾
    return None


def find_leftovers(app_path):
    """按 Pearcleaner 的思路找残留：只看各位置的第一层，按 bundle id / 应用名精确匹配。"""
    info = _plist(app_path)
    bid = info.get("CFBundleIdentifier") or ""
    # 不用 CFBundleExecutable：Tor Browser 的可执行文件叫 firefox、很多 Electron 应用叫 Electron，会误中别的应用的数据
    names = {_norm(os.path.basename(app_path)[:-4]), _norm(info.get("CFBundleName")), _norm(info.get("CFBundleDisplayName"))} - {""}
    team = _team_id(app_path)
    others = [a for a in _find_apps() if os.path.realpath(a) != os.path.realpath(app_path)]
    other_bids = {(_plist(a).get("CFBundleIdentifier") or "").lower() for a in others} - {""}
    other_names = {_norm(os.path.basename(a)[:-4]) for a in others}
    names = {n for n in names if n not in other_names}  # 另一个应用也叫这个名字，就不靠名字匹配
    found, seen = [], set()

    def add(p, why, on, admin=False):
        rp = os.path.realpath(p)
        if rp in seen or os.path.islink(p) or not os.path.lexists(p):
            return
        seen.add(rp)
        found.append({"path": p, "why": why, "on": on and not admin, "admin": admin})

    for loc in USER_LOCATIONS:
        d = os.path.join(LIB, loc)
        try:
            kids = os.listdir(d)
        except OSError:
            continue
        for k in kids:
            hit = _match(k, bid, names, team, other_bids)
            if hit:
                add(os.path.join(d, k), hit[0] + " · ~/Library/" + loc, hit[1])
    # ByHost 里是 bid.<机器 UUID>.plist
    for p in glob.glob(os.path.join(LIB, "Preferences", "ByHost", glob.escape(bid) + ".*.plist")) if bid else []:
        add(p, "bundle id 一致 · ByHost 偏好设置", True)
    for loc in SYSTEM_LOCATIONS:
        try:
            kids = os.listdir(loc)
        except OSError:
            continue
        for k in kids:
            hit = _match(k, bid, names, "", other_bids)
            if hit:
                p = os.path.join(loc, k)
                add(p, hit[0] + " · " + loc, hit[1], admin=not os.access(loc, os.W_OK))
    # 家目录里的 .应用名 文件夹（默认不勾，像 ~/.vscode 这种可能有你自己的配置）
    for k in os.listdir(HOME):
        if k.startswith(".") and len(k) > 4 and _norm(k[1:]) in names:
            add(os.path.join(HOME, k), "名字一致 · 家目录里的隐藏文件夹（可能有你的配置）", False)
    for p in FORCE.get(bid, []):
        p = os.path.expanduser(p)
        add(p, "这个应用已知会用的位置", not os.path.basename(p).startswith("."))
    cask = brew_casks().get(os.path.basename(app_path))
    if cask and BREW:
        room = os.path.join(os.path.dirname(os.path.dirname(BREW)), "Caskroom", cask["token"])
        add(room, "Homebrew 的安装记录（移走后 brew 就不再认为它装着）", True)
    return {"bid": bid, "team": team, "items": found}


# ---------------------------------------------------------------- 计划（先给人看、确认后才动）
PLANS = {}
_PLOCK = threading.Lock()
PLAN_TTL = 3600  # 扫描结果一小时内有效，过了要重新扫


def _prune_plans():
    for k in [k for k, v in PLANS.items() if time.time() - v["at"] > PLAN_TTL]:
        PLANS.pop(k, None)


def _save_run(moved):
    """记下这次移走了什么（存盘，管家重启后也能撤销）。只留最近 30 次。"""
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
    app_path = os.path.normpath(str(app_path or ""))
    if not app_path.endswith(".app") or not any(os.path.dirname(app_path) == d or os.path.dirname(os.path.dirname(app_path)) == d
                                                for d in APP_DIRS) or not os.path.isdir(app_path) or os.path.islink(app_path):
        return {"error": "只能卸载「应用程序」文件夹里的应用"}
    a = next((x for x in list_apps() if x["path"] == app_path), None)
    if not a:
        return {"error": "找不到这个应用"}
    if a["protected"]:
        return {"error": "这是%s，不能卸载" % a["protected"]}
    lo = find_leftovers(app_path)
    items = [{"path": app_path, "why": "应用本体", "on": True, "admin": not os.access(os.path.dirname(app_path), os.W_OK),
              "kind": "app"}] + lo["items"]
    data_locs = [os.path.join(LIB, x) for x in ("Application Support", "Containers", "Group Containers")]
    for it in items:
        it["bytes"] = _du(it["path"])
        if it.get("kind") != "app" and it["bytes"] > 100 * 1048576 and any(_inside(it["path"], d) for d in data_locs):
            it["why"] += " · 可能有你的文档或数据库，请看一眼"
            if it["why"].startswith("名字一致"):
                it["on"] = False  # 只靠名字对上、又很大的，默认不勾
    plan = _keep_plan("uninstall", items, {"app": a})
    return {"id": plan["id"], "app": a, "team": lo["team"], "items": _pub_items(items),
            "note": "需要管理员权限的项目不会自动处理，可以点「在访达中显示」自己拖到废纸篓。" if any(i["admin"] for i in items) else ""}


def _bootout(p):
    """卸载时如果残留里有 LaunchAgent，先让它停下（不删任何东西）。"""
    if p.endswith(".plist") and _inside(p, os.path.join(LIB, "LaunchAgents")):
        _run(["launchctl", "bootout", "gui/%d" % os.getuid(), p], 10)


def execute(plan_id, picks):
    """执行清单里勾选的项目（picks 是序号列表）。只认服务端记下的路径。"""
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
    if plan["kind"] == "uninstall":
        app = plan["app"]
        if any(it.get("kind") == "app" for it in items) and (app["path"] + "/Contents/MacOS/") in _running_apps():
            with _PLOCK:
                PLANS[plan["id"]] = plan
            return {"error": "「%s」还开着，请先退出它" % app["name"], "running": True}
        for it in items:
            _bootout(it["path"])
        title = "开始卸载 %s（你在工具箱里确认后执行）" % app["name"]
    else:
        title = "开始整理文件（你在工具箱里确认后执行）"
    res = _trash_many(items, title)
    run_id = _save_run(res["moved"]) if res["moved"] else None
    _CASKS["at"] = 0
    return {"run_id": run_id, "count": len(res["moved"]), "bytes": res["bytes"],
            "failed": res["failed"], "moved": [_home(m["src"]) for m in res["moved"]]}


def undo(run_id):
    run = _take_run(str(run_id))
    if not run:
        return {"error": "找不到这次操作的记录。东西还在废纸篓里，原位置记在 ~/Library/Logs/deck-tools.log"}
    r = cleaner.undo(run["moved"])
    _log(["撤销：放回 %d 项" % r["restored"]])
    return r


def cancel(plan_id):
    with _PLOCK:
        PLANS.pop(str(plan_id), None)
    return {"ok": True}


def quit_app(app_path):
    a = next((x for x in list_apps() if x["path"] == os.path.normpath(str(app_path or ""))), None)
    if not a or not re.fullmatch(r"[A-Za-z0-9.\-_]{1,200}", a["bid"] or ""):
        return {"error": "找不到这个应用"}
    # 用 bundle id 让它正常退出（会弹「要保存吗」），不强杀
    code, _, err = _run(["osascript", "-e", 'tell application id "%s" to quit' % a["bid"]], 20)
    return {"ok": code == 0, "error": None if code == 0 else "没能退出：%s" % err.strip()[:120]}


def reveal(path):
    p = os.path.expanduser(str(path or ""))
    if not os.path.isabs(p) or not os.path.lexists(p) or "\x00" in p:
        return {"error": "找不到"}
    code, _, _ = _run(["open", "-R", p], 10)
    return {"ok": code == 0}


# ---------------------------------------------------------------- 垃圾清理（参考 Mole 的分类，只移进废纸篓）
def weekly_whitelist():
    """每日清理（cleaner.py「缓存」组）那份缓存清单，两边共用一份，不另抄一遍。11-06 起原来的每周清理脚本并进了每日清理。"""
    try:
        import cleaner
        return [p for r in cleaner.RULES if r.get("group") == "缓存" for p in r.get("paths", [])]
    except Exception:  # noqa: BLE001
        return []


# 不碰：系统的、管家自己的，以及每日清理特意不碰的（语音转写模型）和下载来的大模型/浏览器
JUNK_EXCLUDE = ["com.apple.*", "CloudKit", "com.anthropic.*", "claude-cli-nodejs", "Homebrew", "weekly-cache-clean*",
                "deck-tools*", "DeckVoice*", "Deck", "FamilyCircle", "GeoServices", "familycircled",
                "transcribe", "io.island.whisper*", "*whisper*", "ms-playwright*", "huggingface", "torch", "*model*", "*Model*",
                "pypoetry", "JetBrains", "Google"]


def _junk_rules():
    week = weekly_whitelist()
    return [
        {"id": "weekly", "group": "每日清理清单", "label": "每晚 23:30 自动清的那些缓存（现在就清）", "paths": week, "days": 0, "on": True},
        {"id": "user-caches", "group": "系统垃圾", "label": "应用缓存（7 天没动过的，正在运行的应用会自动重建）",
         "paths": ["~/Library/Caches/*"], "days": 7, "on": True, "exclude": JUNK_EXCLUDE},
        {"id": "user-logs", "group": "系统垃圾", "label": "应用日志（14 天前的）", "paths": ["~/Library/Logs/*"], "days": 14, "on": True,
         "exclude": JUNK_EXCLUDE + ["DiagnosticReports*", "Claude"]},  # DiagnosticReportsForNewHardware 是系统的，没权限移
        {"id": "crash-reports", "group": "系统垃圾", "label": "崩溃报告（30 天前的）", "paths": ["~/Library/Logs/DiagnosticReports/*"], "days": 30, "on": True},
        {"id": "partial-dl", "group": "下载", "label": "没下完的下载", "paths": ["~/Downloads/*.download", "~/Downloads/*.crdownload",
                                                                       "~/Downloads/*.part"], "days": 1, "on": True},
        {"id": "old-installers", "group": "下载", "label": "下载文件夹里 30 天前的安装包（dmg / pkg）", "paths": ["~/Downloads/*.dmg", "~/Downloads/*.pkg",
                                                                                              "~/Downloads/*.xip"], "days": 30, "on": False},
        {"id": "mail-dl", "group": "下载", "label": "邮件附件打开时存的副本（30 天前的；如果你改过打开的附件，改动只在这里）",
         "paths": ["~/Library/Containers/com.apple.mail/Data/Library/Mail Downloads/*", "~/Library/Mail Downloads/*"], "days": 30, "on": False},
        {"id": "xcode-derived", "group": "开发", "label": "Xcode 编译缓存 DerivedData（7 天没动的）", "paths": ["~/Library/Developer/Xcode/DerivedData/*"],
         "days": 7, "on": True},
        {"id": "xcode-devicesupport", "group": "开发", "label": "旧 iOS 设备调试符号（60 天没用的）",
         "paths": ["~/Library/Developer/Xcode/iOS DeviceSupport/*"], "days": 60, "on": False},
        {"id": "sim-caches", "group": "开发", "label": "模拟器缓存", "paths": ["~/Library/Developer/CoreSimulator/Caches/*"], "days": 7, "on": True},
        {"id": "dev-purge", "group": "开发", "label": "90 天没动的项目里的依赖和编译产物（node_modules、.next、target…，需要时重新安装）",
         "find": ["node_modules", ".next", "__pycache__", ".dart_tool", ".gradle", "target", ".build", "DerivedData"],
         "dirs_only": True, "days": 90, "on": False, "purge": True},
    ]


JUNK_RULES = _junk_rules()


def _excluded(p, pats):
    n = os.path.basename(p.rstrip("/"))
    return any(fnmatch.fnmatch(n, x) for x in pats or [])


def junk_scan():
    global JUNK_RULES
    JUNK_RULES = _junk_rules()
    # dev-purge 用 cleaner 的项目内查找（只在 Desktop/Documents/deck-for-mac 里，跳过 git 跟踪的）
    rules = JUNK_RULES
    plan = cleaner.scan(rules)
    # 去掉排除名单里的、以及被别的规则包含的（每周清单里 Caches/Google/Chrome 和 Caches/Google 不重复算）
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
    pub = cleaner.public(plan)
    for r, src in zip(pub["rules"], plan["rules"]):
        r["items"] = [{"path": _home(i["path"]), "bytes": i["bytes"], "age_days": i["age_days"]} for i in src["items"][:200]]
    pub["trash_bytes"] = trash_size()
    return pub


def _purge_ok(p):
    """依赖目录：旁边得有对应的项目文件，才认定是可以重建的产物。"""
    d, n = os.path.dirname(p), os.path.basename(p)
    need = {"node_modules": ["package.json"], ".next": ["package.json"], "target": ["Cargo.toml", "pom.xml"],
            ".build": ["Package.swift"], ".dart_tool": ["pubspec.yaml"], ".gradle": ["build.gradle", "build.gradle.kts", "settings.gradle"],
            "__pycache__": [], "DerivedData": []}.get(n)
    if need is None:
        return False
    if os.path.basename(d) == "node_modules" or "/node_modules/" in p:
        return False
    return not need or any(os.path.exists(os.path.join(d, m)) for m in need)


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
    res = cleaner.run(plan["cplan"], ids, JUNK_RULES, log_title="开始清理（工具箱，你确认后执行）", tag="工具箱垃圾清理",
                      check=_safe_target)
    run_id = _save_run(res["moved"]) if res["moved"] else None
    _save_state({"junk_last": {"at": time.time(), "bytes": res["bytes"], "count": len(res["moved"])}})
    return {"run_id": run_id, "count": len(res["moved"]), "bytes": res["bytes"], "failed": [_home(f) for f in res["failed"]]}


def trash_size():
    """废纸篓多大。没有「完全磁盘访问权限」时 macOS 不让读废纸篓，返回 None。"""
    return _du(TRASH, 20, strict=True)


# ---------------------------------------------------------------- 文件管理：大文件、重复文件、空间分布
# 「文件包」：看起来是一个文件，其实是文件夹。里面的单个文件不能单独动，动了整个资源库或虚拟机就坏了
PACKAGE_EXT = (".app", ".photoslibrary", ".bundle", ".musiclibrary", ".fcpbundle", ".logicx", ".imovielibrary", ".lrdata",
               ".lrlibrary", ".pvm", ".vmwarevm", ".utm", ".sparsebundle", ".band", ".tvlibrary", ".aplibrary", ".xcarchive",
               ".framework", ".plugin", ".kext", ".pkg", ".mpkg", ".rtfd", ".pages", ".numbers", ".key", ".dtBase2", ".bbprojectd")
SF_DATALESS = 0x40000000  # iCloud 里没下载到本机的文件
FILE_SKIP_DIRS = {"Library", ".Trash", "node_modules", ".git", ".venv", "venv", ".cache", ".npm", ".cargo", ".rustup", ".gradle"}


def big_files(min_mb=200, limit=200):
    """先问 Spotlight（快），不行再自己走一遍（最多 40 秒）。不进 ~/Library 和应用包。"""
    min_b = max(10, int(min_mb)) * 1048576
    found = {}
    code, out, _ = _run(["mdfind", "-onlyin", HOME, "kMDItemFSSize >= %d" % min_b], 30)
    if code == 0:
        for p in out.splitlines():
            if _file_ok(p):
                found[p] = 1
    if not found:
        t0 = time.time()
        for root, dirs, files in os.walk(HOME):
            if time.time() - t0 > 40:
                break
            dirs[:] = [d for d in dirs if d not in FILE_SKIP_DIRS and not d.lower().endswith(PACKAGE_EXT)
                       and not (root == HOME and d.startswith("."))]
            for f in files:
                p = os.path.join(root, f)
                try:
                    if os.lstat(p).st_size >= min_b:
                        found[p] = 1
                except OSError:
                    pass
    items = []
    meta = _mdls(list(found)[:500])
    for p in found:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if st.st_size < min_b or not os.path.isfile(p) or os.path.islink(p) or not _file_ok(p):
            continue
        items.append({"path": p, "bytes": st.st_size, "mtime": st.st_mtime, "last_used": meta.get(p, {}).get("last_used"),
                      "dataless": bool(getattr(st, "st_flags", 0) & SF_DATALESS), "ino": (st.st_dev, st.st_ino)})
    items.sort(key=lambda x: -x["bytes"])
    return items[:limit]


def _file_ok(p):
    if not _inside(p, HOME) or _inside(p, LIB) or _inside(p, TRASH):
        return False
    parts = p[len(HOME) + 1:].split("/")
    if parts and parts[0].startswith("."):
        return False
    return not any(x in FILE_SKIP_DIRS or x.lower().endswith(PACKAGE_EXT) for x in parts[:-1])


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
        # 没下载到本机的 iCloud 文件不读（会触发下载）；同一个文件的硬链接只算一次
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


def _du_deadline(p, deadline):
    """du -sk，到点就放弃（被 iCloud 占位文件、网络盘卡住的 du 可能杀不死，不在这里等它退出）。"""
    if os.path.islink(p) or not os.path.isdir(p):
        return _du(p, 5)
    try:
        proc = subprocess.Popen(["du", "-sk", p], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    while proc.poll() is None:
        if time.time() > deadline:
            proc.kill()
            threading.Thread(target=proc.wait, daemon=True).start()  # 后台收尸，不卡住这次扫描
            return None
        time.sleep(0.1)
    try:
        return int(proc.stdout.read().split()[0]) * 1024
    except (IndexError, ValueError, OSError):
        return None


def space_overview(progress=None):
    """家目录下第一层文件夹各占多少：4 个 du 并行，整次最多 60 秒；超时的标「太大没算完」。"""
    try:
        names = [n for n in os.listdir(HOME) if n != ".Trash"]
    except OSError:
        return []
    dirs = [os.path.join(HOME, n) for n in names]
    dirs = [p for p in dirs if not os.path.islink(p) and os.path.isdir(p)]
    deadline = time.time() + 60
    out, done = [], [0]
    lock = threading.Lock()

    def one(p):
        b = _du_deadline(p, deadline)
        with lock:
            done[0] += 1
            out.append({"path": _home(p), "bytes": b, "slow": b is None})
            if progress:
                progress("算到第 %d/%d 个文件夹" % (done[0], len(dirs)))

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, dirs))
    out.append({"path": "~/.Trash（废纸篓）", "bytes": _du_deadline(TRASH, time.time() + 15)})
    out.sort(key=lambda x: -(x["bytes"] or 0))
    return out[:20]


def files_scan(min_mb=200):
    files = big_files(min_mb)
    dups = duplicates(files)
    now = time.time()
    items = []
    synced = [os.path.join(HOME, d) for d in ("Dropbox", "OneDrive", "Google Drive", "Nutstore Files")]
    if os.path.isdir(os.path.join(LIB, "Mobile Documents", "com~apple~CloudDocs", "Desktop")):
        synced += [os.path.join(HOME, "Desktop"), os.path.join(HOME, "Documents")]  # 开了 iCloud「桌面与文稿」
    for f in files:
        used = f.get("last_used") or f["mtime"]
        why = "大文件" + (" · 只在 iCloud 里，没下载到本机" if f.get("dataless") else "")
        if any(_inside(f["path"], d) for d in synced):
            why += " · 在同步文件夹里，移走后其他设备上也会没有"
        items.append({"path": f["path"], "bytes": f["bytes"], "why": why, "on": False, "kind": "big",
                      "age_days": int((now - used) / DAY)})
    dup_idx = []
    for g in dups:
        # 每组最早的那份留着，其余的列出来（默认不勾）
        dup_idx.append([next(i for i, it in enumerate(items) if it["path"] == f["path"]) for f in g])
    plan = _keep_plan("files", items)
    return {"id": plan["id"], "items": _pub_items(items), "dups": dup_idx, "min_mb": min_mb}


# ---------------------------------------------------------------- 更新（参考 Latest：Sparkle + App Store；再加 Homebrew）
def _vkey(v):
    parts = re.findall(r"\d+|[a-z]+", str(v or "").lower())
    out = []
    for p in parts:
        out.append((1, int(p), "") if p.isdigit() else (0, 0, p))  # 1.0b1 < 1.0
    return out


def newer(a, b):
    """a 比 b 新？"""
    if not a or not b:
        return False
    return _vkey(a) > _vkey(b)


def _fetch(url, limit=2 << 20, timeout=10):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("只查 https 的更新源")
    req = urllib.request.Request(url, headers={"User-Agent": "MacButler/1.0 (update check)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  只允许 https
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("更新源太大")
    return data


def _appcast_latest(url):
    """读 Sparkle appcast，返回最高的 (显示版本, 内部版本)。拒绝带 DOCTYPE/ENTITY 的 XML（防实体炸弹）。"""
    import xml.etree.ElementTree as ET
    data = _fetch(url)
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise ValueError("更新源格式不安全")
    root = ET.fromstring(data)
    sp = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
    best = None
    for item in root.iter("item"):
        enc = item.find("enclosure")
        short = item.findtext(sp + "shortVersionString") or (enc.get(sp + "shortVersionString") if enc is not None else None)
        ver = item.findtext(sp + "version") or (enc.get(sp + "version") if enc is not None else None)
        if item.findtext(sp + "channel"):  # beta 频道不算
            continue
        key = short or ver
        if key and (best is None or newer(key, best[0])):
            best = (short or ver, ver)
    return best


def _mas_lookup(bids):
    out = {}
    for i in range(0, len(bids), 40):
        chunk = bids[i:i + 40]
        for country in ("cn", "us"):
            want = [b for b in chunk if b not in out]
            if not want:
                break
            try:
                j = json.loads(_fetch("https://itunes.apple.com/lookup?" + urllib.parse.urlencode(
                    {"bundleId": ",".join(want), "country": country, "entity": "macSoftware"})))
            except Exception:  # noqa: BLE001
                continue
            for r in j.get("results", []):
                if r.get("bundleId") in want and r.get("kind") == "mac-software":  # 同一个 bundle id 可能也是 iOS 应用
                    out[r["bundleId"]] = {"version": r.get("version"), "id": r.get("trackId"), "notes": (r.get("releaseNotes") or "")[:400]}
    return out


def updates_check(progress=None):
    say = progress or (lambda s: None)
    apps = [a for a in list_apps() if not a["protected"]]
    res = {"at": time.time(), "brew": [], "formula": [], "sparkle": [], "mas": [], "unknown": [], "errors": []}
    env = dict(os.environ, HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_AUTO_UPDATE="1")
    if BREW:
        say("更新 Homebrew 索引…")
        _run([BREW, "update", "--quiet"], 180, env=env)
        say("问 Homebrew 有哪些能更新…")
        code, out, err = _run([BREW, "outdated", "--json=v2", "--greedy"], 120, env=env)
        try:
            j = json.loads(out)
            for c in j.get("casks", []):
                res["brew"].append({"token": c.get("name"), "from": ", ".join(c.get("installed_versions") or []) if isinstance(c.get("installed_versions"), list)
                                    else c.get("installed_versions"), "to": c.get("current_version")})
            for f in j.get("formulae", []):
                if not f.get("pinned"):
                    res["formula"].append({"token": f.get("name"), "from": ", ".join(f.get("installed_versions") or []), "to": f.get("current_version")})
        except ValueError:
            res["errors"].append("Homebrew：%s" % (err.strip()[:160] or "没有返回结果"))
    brew_apps = {a["brew"] for a in apps if a["brew"]}
    mas = [a for a in apps if a["mas"] and a["bid"]]
    if mas:
        say("问 App Store…")
        look = _mas_lookup([a["bid"] for a in mas])
        for a in mas:
            l = look.get(a["bid"])
            if l and newer(l["version"], a["version"]):
                res["mas"].append({"name": a["name"], "path": a["path"], "from": a["version"], "to": l["version"], "id": l["id"], "notes": l["notes"]})
    for a in apps:
        if a["mas"] or a["brew"] in brew_apps and a["brew"]:
            continue
        feed = _plist(a["path"]).get("SUFeedURL")
        if not feed:
            if not a["bid"].startswith("com.apple.Safari.WebApp."):
                res["unknown"].append(a["name"])
            continue
        say("查 %s 的更新源…" % a["name"])
        try:
            best = _appcast_latest(str(feed))
        except Exception as e:  # noqa: BLE001
            res["errors"].append("%s：%s" % (a["name"], str(e)[:80]))
            continue
        info = _plist(a["path"])
        cur_s, cur_v = str(info.get("CFBundleShortVersionString") or ""), str(info.get("CFBundleVersion") or "")
        if best and (newer(best[1], cur_v) if best[1] and cur_v else newer(best[0], cur_s)):
            res["sparkle"].append({"name": a["name"], "path": a["path"], "from": cur_s or cur_v, "to": best[0]})
    _save_state({"updates": res})
    return res


def _clone_backup(app_path, version):
    """升级前用 APFS 克隆把旧版复制一份进废纸篓（不占额外空间），这样升级不会让旧版就此消失。"""
    dst = cleaner._trash_target(app_path[:-4] + "（旧版 %s）.app" % re.sub(r"[^\w.\-]", "", version or "")[:20])
    code, _, err = _run(["cp", "-cR", app_path, dst], 300)
    return dst if code == 0 else None


def brew_upgrade(token, kind, progress=None):
    """用 Homebrew 升级一个 cask 或 formula。旧版：cask 先克隆进废纸篓；formula 设 HOMEBREW_NO_INSTALL_CLEANUP 让 brew 留着旧版本。"""
    say = progress or (lambda s: None)
    if not BREW or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9@+._\-/]{1,100}", token) or token.startswith("-"):
        return {"error": "参数不对"}
    st = _load_state().get("updates") or {}
    known = {u["token"] for u in st.get("brew" if kind == "cask" else "formula", [])}
    if token not in known:
        return {"error": "先检查一次更新"}
    # 不跳过「依赖它的其他软件」检查：比如升级 openssl / icu4c 时 brew 会顺带重装依赖它的工具，免得它们链接不到新库
    env = dict(os.environ, HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_CLEANUP="1")
    backup = None
    if kind == "cask":
        # brew 升级 cask 时会删掉旧版。先把旧版的每个应用克隆进废纸篓；找不到要备份的应用就不升级
        _CASKS["at"] = 0
        apps = [(n, c) for n, c in brew_casks().items() if c["token"] == token]
        paths = [(os.path.join(d, n), c) for n, c in apps for d in APP_DIRS if os.path.isdir(os.path.join(d, n))]
        if not paths:
            return {"error": "这个 Homebrew 软件不是普通应用（或找不到它装在哪），为了不丢旧版，请自己在终端里更新"}
        ps = _running_apps()
        for p, c in paths:
            if (p + "/Contents/MacOS/") in ps:
                return {"error": "「%s」还开着，请先退出它再更新" % os.path.basename(p)[:-4]}
        backups = []
        for p, c in paths:
            say("把旧版克隆一份到废纸篓…")
            b = _clone_backup(p, c.get("version"))
            if not b:
                return {"error": "没能先备份旧版，没有更新"}
            backups.append(b)
        backup = "、".join(backups)
    say("brew upgrade %s…" % token)
    cmd = [BREW, "upgrade", "--cask", token] if kind == "cask" else [BREW, "upgrade", "--formula", token]
    code, out, err = _run(cmd, 3600, env=env)  # 给足时间，免得升级到一半被掐断
    _CASKS["at"] = 0
    _log(["更新 %s（%s）：%s%s" % (token, kind, "成功" if code == 0 else "失败", "；旧版备份 " + backup if backup else "")])
    return {"ok": code == 0, "backup": backup.replace(HOME, "~") if backup else None,
            "output": (out + err)[-1500:], "error": None if code == 0 else "更新失败，详情看输出"}


def open_update(kind, ref):
    """Sparkle 应用：打开它，让它自己检查更新；App Store：打开商店页。"""
    if kind == "mas":
        try:
            tid = int(ref)
        except (TypeError, ValueError):
            return {"error": "参数不对"}
        code, _, _ = _run(["open", "macappstore://apps.apple.com/app/id%d" % tid], 10)
        return {"ok": code == 0}
    a = next((x for x in list_apps() if x["path"] == os.path.normpath(str(ref or ""))), None)
    if not a:
        return {"error": "找不到这个应用"}
    code, _, _ = _run(["open", a["path"]], 10)
    return {"ok": code == 0, "note": "已经打开「%s」，在它的菜单里选「检查更新」" % a["name"]}


# ---------------------------------------------------------------- 后台任务（扫描大文件、检查更新、brew 升级都可能要几分钟）
JOBS = {}
_JLOCK = threading.Lock()


def start_job(kind, fn, *args):
    with _JLOCK:
        for j in JOBS.values():
            if j["kind"] == kind and j["state"] == "running":
                if kind == "upgrade" and j.get("args") != args:
                    return None  # 一次只升级一个，别把别的软件的任务当成这次的
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
            "updates_at": up.get("at"), "update_names": [u.get("token") or u.get("name") for u in
                                                         (up.get("brew", []) + up.get("sparkle", []) + up.get("mas", []))][:6],
            "junk_last": st.get("junk_last"), "brew": bool(BREW)}
