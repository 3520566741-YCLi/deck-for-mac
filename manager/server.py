#!/usr/bin/env python3
"""Mac 管家 · 自制应用本地管理器 + 监控台

默认只监听 127.0.0.1。本地状态随时刷新（免费）；DeepSeek 排查只在点按钮时跑一次。
监控台 /dash 可选开放给同一 Wi-Fi 下的 iPad：只读，凭配对密钥访问；管理操作永远只允许本机。
只用 Python 标准库，兼容 macOS 自带的 python3 (3.9+)。
"""
import glob
import hmac
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# 监控台用到的第三方前端库（固定版本，放在本地，断网也能用）
STATIC_FILES = {"/static/gridstack-all.js": "application/javascript; charset=utf-8",
                "/static/gridstack.min.css": "text/css; charset=utf-8",
                "/static/deck-voice.js": "application/javascript; charset=utf-8",
                "/static/deck-tools.js": "application/javascript; charset=utf-8",
                "/static/deck-assist.js": "application/javascript; charset=utf-8",
                # Vesper 光球的两个动画层，来自 React Bits（MIT + Commons Clause，见 static/reactbits-LICENSE.md）
                "/static/reactbits-orb.js": "application/javascript; charset=utf-8",
                "/static/reactbits-strands.js": "application/javascript; charset=utf-8",
                # Deck 按键音效（11-07）：脚本 + 11 个短音（SND01 / Kenney CC0，见 static/sfx/LICENSE.md）
                "/static/deck-sfx.js": "application/javascript; charset=utf-8"}
STATIC_FILES.update({"/static/sfx/%s.wav" % n: "audio/wav" for n in
                     ("tap", "primary", "tab", "on", "off", "open", "close", "page", "done", "notify", "warn")})  # 语音层客户端库（语音线程提供）
HOME = os.path.expanduser("~")
TOKEN = secrets.token_hex(16)
HISTORY = os.path.join(HERE, "check-history.json")


def load_json(name, default):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_json("config.json", load_json("config.example.json", {}))
PORT = int(CONFIG.get("port", 8777))
DASH_CFG = CONFIG.get("dashboard", {})
LAN = bool(DASH_CFG.get("lan"))
PAIR_FILE = os.path.join(HERE, "pair.key")
LOCAL_ADDRS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def pair_key():
    """iPad 配对密钥：第一次用时随机生成，存在 manager/pair.key（已在 .gitignore 里）。"""
    if os.path.exists(PAIR_FILE):
        with open(PAIR_FILE, encoding="utf-8") as f:
            k = f.read().strip()
        if len(k) >= 16:
            return k
    k = secrets.token_urlsafe(18)
    fd = os.open(PAIR_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k)
    return k


PAIR_KEY = pair_key()


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def x(path):
    return os.path.expanduser(path) if path else path


# ---------- 进程 ----------

def ps_table():
    """返回 [{pid, ppid, cpu, rss_mb, etime, cmd}]，cmd 是完整可执行路径。"""
    code, out, _ = run(["ps", "-axww", "-o", "pid=,ppid=,%cpu=,rss=,etime=,comm="])
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            rows.append({
                "pid": int(parts[0]), "ppid": int(parts[1]), "cpu": float(parts[2]),
                "rss_mb": round(int(parts[3]) / 1024, 1), "etime": parts[4], "cmd": parts[5],
            })
        except ValueError:
            continue
    return rows


def _to_mb(v, unit):
    v, unit = float(v), unit.upper()
    return v / 1024 if unit == "KB" else v * 1024 if unit == "GB" else v / 1048576 if unit == "B" else v


GPU_CATEGORIES = ("IOSurface", "IOAccelerator", "IOAccelerator (graphics)")


def footprint_detail(pid):
    """返回 (phys_footprint_mb, gpu_mb)。Apple 芯片是统一内存：footprint 含显存；
    gpu_mb 是其中 IOSurface + IOAccelerator 两类脏页之和，可近似看作该进程占的显存。"""
    if not os.path.exists("/usr/bin/footprint"):
        return None, None
    code, out, _ = run(["/usr/bin/footprint", "-p", str(pid)], timeout=8)
    if code != 0 or not out:
        return None, None
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*(KB|MB|GB|B)\b", out) or \
        re.search(r"Footprint:\s*([\d.]+)\s*(KB|MB|GB|B)\b", out)
    total = round(_to_mb(*m.groups()), 1) if m else None
    gpu = 0.0
    # 只算主进程段（第一个 TOTAL 之前），避免把共享段重复计入
    head = out.split("TOTAL", 1)[0]
    for v, unit, cat in re.findall(r"^\s*([\d.]+)\s*(KB|MB|GB|B)\s+\S+\s+\S+\s+\S+\s+\S+\s+\d+\s+(.+?)\s*$",
                                   head, re.M):
        if cat in GPU_CATEGORIES:
            gpu += _to_mb(v, unit)
    return total, round(gpu, 1)


def footprint_mb(pid):
    return footprint_detail(pid)[0]


def descendants(rows, root_pids):
    kids, frontier = set(root_pids), list(root_pids)
    while frontier:
        p = frontier.pop()
        for r in rows:
            if r["ppid"] == p and r["pid"] not in kids:
                kids.add(r["pid"])
                frontier.append(r["pid"])
    return kids


# ---------- 系统 ----------

def gpu_stats():
    code, out, _ = run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"])
    stats = {}
    m = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}', out)
    if m:
        for k, v in re.findall(r'"([^"]+)"=(\d+)', m.group(1)):
            stats[k] = int(v)
    res = {}
    if "Device Utilization %" in stats:
        res["utilization_pct"] = stats["Device Utilization %"]
    if "In use system memory" in stats:
        res["in_use_mb"] = round(stats["In use system memory"] / 1048576)
    if "Alloc system memory" in stats:
        res["alloc_mb"] = round(stats["Alloc system memory"] / 1048576)
    return res


def memory_stats():
    code, out, _ = run(["sysctl", "-n", "hw.memsize"])
    total = int(out.strip() or 0)
    code, vm, _ = run(["vm_stat"])
    page = 16384
    m = re.search(r"page size of (\d+)", vm)
    if m:
        page = int(m.group(1))
    pages = {k.strip(): int(v.strip(" .")) for k, v in re.findall(r"^([^:]+):\s+(\d+)\.?$", vm, re.M)}
    used = (pages.get("Pages active", 0) + pages.get("Pages wired down", 0)
            + pages.get("Pages occupied by compressor", 0)) * page
    code, mp, _ = run(["memory_pressure", "-Q"], timeout=5)
    free_pct = None
    m = re.search(r"free percentage:\s*(\d+)%", mp)
    if m:
        free_pct = int(m.group(1))
    return {"total_gb": round(total / 2**30, 1), "used_gb": round(used / 2**30, 1), "free_pct": free_pct}


# ---------- LaunchAgent / Dock / 崩溃 ----------

VENDOR_PREFIX = ("com.apple.", "com.google.", "com.microsoft.", "com.adobe.", "com.valvesoftware.",
                 "us.zoom.", "com.tencent.", "com.spotify.", "org.pqrs.", "com.jetbrains.", "io.tailscale.")


def launch_agents():
    uid = os.getuid()
    items = []
    for p in sorted(glob.glob(os.path.join(HOME, "Library/LaunchAgents/*.plist"))):
        try:
            with open(p, "rb") as f:
                d = plistlib.load(f)
        except Exception:  # noqa: BLE001
            items.append({"label": os.path.basename(p), "plist": p, "error": "plist 读不出来"})
            continue
        label = d.get("Label", os.path.basename(p)[:-6])
        if label.startswith(VENDOR_PREFIX):
            continue
        code, out, _ = run(["launchctl", "print", "gui/%d/%s" % (uid, label)], timeout=5)
        loaded = code == 0
        pid = re.search(r"\bpid = (\d+)", out)
        last = re.search(r"last exit code = (-?\d+|\(never exited\))", out)
        runs = re.search(r"\bruns = (\d+)", out)
        prog = d.get("Program") or (d.get("ProgramArguments") or [""])[0]
        items.append({
            "label": label, "plist": p, "loaded": loaded,
            "pid": int(pid.group(1)) if pid else None,
            "last_exit": last.group(1) if last else None,
            "runs": int(runs.group(1)) if runs else None,
            "program": prog, "program_exists": bool(prog) and os.path.exists(x(prog)),
            "keep_alive": bool(d.get("KeepAlive")), "run_at_load": bool(d.get("RunAtLoad")),
        })
    return items


def dock_items():
    code, out, _ = run(["defaults", "export", "com.apple.dock", "-"])
    try:
        d = plistlib.loads(out.encode())
    except Exception:  # noqa: BLE001
        return []
    res = []
    for section in ("persistent-apps", "persistent-others"):
        for t in d.get(section, []):
            td = t.get("tile-data", {})
            url = (td.get("file-data") or {}).get("_CFURLString", "")
            path = urllib.parse.unquote(url.replace("file://", "")).rstrip("/") if url.startswith("file://") else url
            res.append({"section": section, "label": td.get("file-label", "?"), "path": path,
                        "exists": bool(path) and os.path.exists(path)})
    return res


def recent_crashes(proc, days=7):
    if not proc:
        return []
    cutoff = time.time() - days * 86400
    out = []
    for d in (os.path.join(HOME, "Library/Logs/DiagnosticReports"), "/Library/Logs/DiagnosticReports"):
        for p in glob.glob(os.path.join(d, "*")):
            name = os.path.basename(p)
            if not name.lower().startswith(proc.lower().replace(" ", "")[:12]) and not name.startswith(proc):
                continue
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt >= cutoff:
                out.append({"file": p, "time": time.strftime("%m-%d %H:%M", time.localtime(mt))})
    return sorted(out, key=lambda c: c["time"], reverse=True)[:5]


def crash_excerpt(path, limit=1200):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read(200000)
    except OSError:
        return ""
    keep = []
    for key in ("exception", "termination", "crashed", "dyld", "reason", "signal", "faultingThread"):
        for m in re.finditer(r'.{0,80}"?%s"?.{0,200}' % key, txt, re.I):
            keep.append(m.group(0))
    return "\n".join(dict.fromkeys(keep))[:limit]


def url_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return None


def app_info(path):
    try:
        with open(os.path.join(path, "Contents/Info.plist"), "rb") as f:
            d = plistlib.load(f)
    except Exception:  # noqa: BLE001
        return {}
    exe = d.get("CFBundleExecutable", "")
    return {"bundle_id": d.get("CFBundleIdentifier"), "version": d.get("CFBundleShortVersionString"),
            "exe_ok": bool(exe) and os.path.exists(os.path.join(path, "Contents/MacOS", exe))}


# ---------- 汇总 ----------

def collect(deep=False):
    registry = load_json("apps.json", load_json("apps.example.json", {"apps": []}))["apps"]
    rows = ps_table()
    agents = launch_agents()
    agent_by_label = {a["label"]: a for a in agents}
    dock = dock_items()
    dock_paths = {d["path"] for d in dock}
    apps = []
    for a in registry:
        item = dict(a)
        issues = []
        pids = []
        if a.get("kind") in ("app", "folder"):
            path = x(a["path"])
            item["installed"] = os.path.exists(path)
            if not item["installed"]:
                issues.append("没装：%s 不存在" % path)
            elif path.endswith(".app"):
                item.update(app_info(path))
                if not item.get("exe_ok"):
                    issues.append(".app 里缺可执行文件（空壳）")
            item["in_dock"] = path.rstrip("/") in dock_paths
            roots = [r["pid"] for r in rows if a["match"] in r["cmd"]]
            pids = sorted(descendants(rows, roots))
        if a.get("kind") == "agent":
            ag = agent_by_label.get(a["label"])
            item["agent"] = ag
            if not ag:
                issues.append("找不到 LaunchAgent %s" % a["label"])
            else:
                if not ag["loaded"]:
                    issues.append("LaunchAgent 没加载")
                if ag.get("pid"):
                    pids = sorted(descendants(rows, [ag["pid"]]))
                if ag.get("last_exit") not in (None, "0", "(never exited)"):
                    issues.append("上次退出码 %s" % ag["last_exit"])
        procs = [r for r in rows if r["pid"] in pids]
        item["running"] = bool(procs)
        item["pids"] = pids
        item["cpu"] = round(sum(r["cpu"] for r in procs), 1)
        item["rss_mb"] = round(sum(r["rss_mb"] for r in procs), 1)
        item["uptime"] = procs[0]["etime"] if procs else None
        if deep and procs:
            det = [footprint_detail(p) for p in pids[:12]]
            fps = [f for f, _ in det if f is not None]
            gps = [g for _, g in det if g is not None]
            item["footprint_mb"] = round(sum(fps), 1) if fps else None
            item["gpu_mb"] = round(sum(gps), 1) if gps else None
        if a.get("url"):
            item["url_status"] = url_ok(a["url"])
            if item["url_status"] is None or item["url_status"] >= 500:
                issues.append("本地服务 %s 无响应" % a["url"])
        if a.get("log"):
            lp = x(a["log"])
            if os.path.exists(lp):
                item["log_mb"] = round(os.path.getsize(lp) / 1048576, 1)
                if item["log_mb"] > 5:
                    issues.append("日志 %.1f MB" % item["log_mb"])
        item["crashes"] = recent_crashes(a.get("proc"))
        if item["crashes"]:
            issues.append("近 7 天崩溃 %d 次" % len(item["crashes"]))
        if a.get("want_running") and not item["running"] and item.get("installed", True):
            issues.append("应该常驻，但现在没在运行")
        item["issues"] = issues
        item["level"] = "error" if any(k in " ".join(issues) for k in ("空壳", "没装", "找不到", "无响应", "没加载")) \
            else ("warn" if issues else "ok")
        apps.append(item)
    tracked_labels = {a.get("label") for a in registry}
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "memory": memory_stats(),
        "gpu": gpu_stats(),
        "apps": apps,
        "dock": dock,
        "dock_broken": [d for d in dock if not d["exists"] and d["path"].startswith("/")],
        "other_agents": [g for g in agents if g["label"] not in tracked_labels],
    }


# ---------- DeepSeek ----------

_KEY = {"at": 0.0, "v": None}


def deepseek_key():
    """读 key 要调 security（每次几十到上百毫秒），对话时这点也算延迟：在内存里留 5 分钟。"""
    if _KEY["v"] and time.time() - _KEY["at"] < 300:
        return _KEY["v"]
    _KEY["v"], _KEY["at"] = _deepseek_key(), time.time()
    return _KEY["v"]


def _deepseek_key():
    return _key_from(CONFIG.get("deepseek", {}).get("key_source", []))


# Jev（TypeSafe 快速选择模型）：Vesper 操作屏幕时用来挑元素。没配就让 Vesper 自己挑。
JEV_SOURCES = [{"type": "env", "name": "TYPESAFE_API_KEY"}, {"type": "keychain", "service": "typesafe-api"}]
_JEV = {"at": 0.0, "v": None}


def jev_key():
    if time.time() - _JEV["at"] < 300:
        return _JEV["v"]
    v = _key_from(CONFIG.get("jev", {}).get("key_source") or JEV_SOURCES)
    # 没找到就不缓存：他刚把 key 放进钥匙串，下一次就能用上，不用重启服务
    _JEV["v"], _JEV["at"] = v, (time.time() if v else 0.0)
    return v


def _key_from(sources):
    for src in sources:
        t = src.get("type")
        if t == "env" and os.environ.get(src.get("name", "")):
            return os.environ[src["name"]].strip()
        if t == "keychain":
            args = ["security", "find-generic-password", "-s", src["service"], "-w"]
            if src.get("account"):
                args[3:3] = ["-a", src["account"]]
            code, out, _ = run(args, timeout=5)
            if code == 0 and out.strip():
                return out.strip()
        if t == "file" and os.path.exists(x(src.get("path", ""))):
            with open(x(src["path"]), encoding="utf-8") as f:
                txt = f.read().strip()
            if src.get("json_key"):
                txt = json.loads(txt).get(src["json_key"], "")
            elif src.get("pattern"):
                m = re.search(src["pattern"], txt, re.M)
                txt = m.group(1).strip() if m else ""
            if txt:
                return txt
    return None


SYSTEM_PROMPT = """你是 Mac 自制应用的运维排查助手。用户给你一份本机快照（JSON），包含每个自制应用的
安装/运行状态、内存、CPU、LaunchAgent 状态、崩溃摘要、Dock 失效项。逐个应用判断它是否健康、
哪里有问题、最可能的原因、用户下一步该做的最小动作（给具体命令或系统设置路径）。不要编造快照里没有的数据。
只输出 JSON：{"summary": "一句话总评", "apps": [{"id": "...", "level": "ok|warn|error",
"finding": "发现（中文，≤60字）", "fix": "建议（中文，≤80字，没问题写“无需处理”）"}],
"dock": "Dock 失效项的处理建议（没有就写“无”）"}"""


def deepseek_check():
    key = deepseek_key()
    if not key:
        return {"error": "找不到 DeepSeek API key。请按 README 在 config.json 的 key_source 里配置。"}
    snap = collect(deep=True)
    slim = {"time": snap["time"], "memory": snap["memory"], "gpu": snap["gpu"],
            "dock_broken": snap["dock_broken"], "apps": [], "other_agents": [
                {k: g.get(k) for k in ("label", "loaded", "last_exit", "program_exists")} for g in snap["other_agents"]]}
    for a in snap["apps"]:
        s = {k: a.get(k) for k in ("id", "name", "kind", "installed", "running", "want_running", "cpu", "rss_mb",
                                   "footprint_mb", "gpu_mb", "uptime", "version", "exe_ok", "in_dock", "url_status",
                                   "log_mb", "issues", "note")}
        if a.get("agent"):
            s["agent"] = {k: a["agent"].get(k) for k in ("loaded", "pid", "last_exit", "runs", "program_exists")}
        if a.get("crashes"):
            s["crash"] = crash_excerpt(a["crashes"][0]["file"])
        slim["apps"].append(s)
    ds = CONFIG.get("deepseek", {})
    body = json.dumps({
        "model": ds.get("model", "deepseek-chat"),
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": json.dumps(slim, ensure_ascii=False)}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(ds.get("base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions",
                                 data=body, headers={"Authorization": "Bearer " + key,
                                                     "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "DeepSeek 说这个 API key 无效（HTTP 401）。请换一个有效 key：在终端执行 "
                             "security add-generic-password -U -s deepseek-api -a \"$USER\" -w ，"
                             "按提示粘贴新 key（存进钥匙串；钥匙串在 key_source 里排在凭据文件前面，会优先使用）。"}
        return {"error": "DeepSeek 返回 HTTP %d：%s" % (e.code, e.read()[:300].decode(errors="replace"))}
    except Exception as e:  # noqa: BLE001
        return {"error": "调用 DeepSeek 失败：%s" % e}
    content = resp["choices"][0]["message"]["content"]
    try:
        result = json.loads(content)
    except ValueError:
        result = {"summary": content, "apps": []}
    result["usage"] = resp.get("usage", {})
    result["seconds"] = round(time.time() - t0, 1)
    result["time"] = snap["time"]
    result["snapshot"] = snap
    hist = []
    if os.path.exists(HISTORY):
        try:
            with open(HISTORY, encoding="utf-8") as f:
                hist = json.load(f)
        except ValueError:
            hist = []
    hist.append({k: result.get(k) for k in ("time", "summary", "apps", "dock", "usage", "seconds")})
    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump(hist[-50:], f, ensure_ascii=False, indent=1)
    return result


# ---------- 动作 ----------

def do_action(app_id, action):
    reg = {a["id"]: a for a in load_json("apps.json", load_json("apps.example.json", {"apps": []}))["apps"]}
    a = reg.get(app_id)
    if not a:
        return {"error": "未知应用"}
    if a.get("kind") == "agent":
        uid = os.getuid()
        if action == "restart":
            code, out, err = run(["launchctl", "kickstart", "-k", "gui/%d/%s" % (uid, a["label"])])
            return {"ok": code == 0, "msg": err or out}
        return {"error": "服务只支持重启"}
    path = x(a["path"])
    if action == "open":
        code, out, err = run(["open", path])
    elif action == "quit":
        name = os.path.basename(path).replace(".app", "")
        code, out, err = run(["osascript", "-e", 'quit app "%s"' % name])
    elif action == "reveal":
        code, out, err = run(["open", "-R", path])
    else:
        return {"error": "未知动作"}
    return {"ok": code == 0, "msg": (err or out).strip()}


# ---------- 监控台 ----------

from monitor import Hub  # noqa: E402

HUB = Hub(DASH_CFG, butler_collect=collect, deepseek_key=deepseek_key)

# ---------- Vesper（Deck 内置 DeepSeek 助手） ----------
from assistant import Friday, assistant_name as _assistant_name  # noqa: E402
from cleaner import public as cleaner_public  # noqa: E402
import daily_clean  # noqa: E402
import apptools  # noqa: E402
import kbclean  # noqa: E402


def tools_api(op, p):
    """工具箱：卸载 / 垃圾 / 文件 / 更新 / 键盘清洁。所有会动文件的操作都只认服务端记下的清单序号，且只移进废纸篓。"""
    s = lambda k: str(p.get(k) or "")  # noqa: E731
    if op == "apps":
        return {"apps": apptools.list_apps()}
    if op == "clean/status":
        return kbclean.status()
    if op == "clean/start":
        return kbclean.start(p.get("seconds") or 90, bool(p.get("black", True)), bool(p.get("mouse")), p.get("skin"), p.get("theme"))
    if op == "clean/stop":
        return kbclean.stop()
    if op == "clean/extend":
        return kbclean.extend()
    if op == "clean/settings":
        return kbclean.open_settings()
    if op == "uninstall/plan":
        return apptools.uninstall_plan(s("path"))
    if op == "quit":
        return apptools.quit_app(s("path"))
    if op == "execute":
        picks = p.get("picks")
        return apptools.execute(s("id"), picks if isinstance(picks, list) else [])
    if op == "cancel":
        return apptools.cancel(s("id"))
    if op == "undo":
        return apptools.undo(s("run_id"))
    if op == "reveal":
        return apptools.reveal(s("path"))
    if op == "junk/scan":
        return {"job": apptools.start_job("junk", apptools.junk_scan)}
    if op == "junk/run":
        rules = p.get("rules")
        return apptools.junk_execute(s("id"), rules if isinstance(rules, list) else [])
    if op == "files/scan":
        try:
            mb = min(100000, max(50, int(p.get("min_mb") or 200)))
        except (TypeError, ValueError):
            mb = 200
        return {"job": apptools.start_job("files", apptools.files_scan, mb)}
    if op == "space":
        return {"job": apptools.start_job("space", apptools.space_overview)}
    if op == "updates/check":
        return {"job": apptools.start_job("updates", apptools.updates_check)}
    if op == "updates/last":
        return apptools._load_state().get("updates") or {}
    if op == "updates/brew":
        kind = "cask" if s("kind") == "cask" else "formula"
        jid = apptools.start_job("upgrade", apptools.brew_upgrade, s("token"), kind)
        return {"job": jid} if jid else {"error": "正在更新另一个软件，等它完成再点"}
    if op == "updates/open":
        return apptools.open_update(s("kind"), p.get("ref"))
    if op == "job":
        return apptools.job(s("id"))
    return {"error": "没有这个操作"}
import procs  # noqa: E402
import diskwatch  # noqa: E402
import hands  # noqa: E402
import ipad_sfx  # noqa: E402  按键音效经数据线送到 iPad（11-07）
ADVICE = {}  # kind -> (时间, 建议)，1 分钟内同一种不重复问 DeepSeek

FRIDAY = Friday(HUB, deepseek_key, CONFIG.get("deepseek", {}))
HANDS = hands.Hands(jev_key)
FRIDAY.hands = HANDS
diskwatch.start()  # 磁盘「谁在占空间」：每小时量一次目录大小、每 30 秒记一次各应用写了多少（只读、不联网）
daily_clean.start(deepseek_key, CONFIG.get("deepseek", {}), FRIDAY, on_change=lambda: HUB.refresh("cleanup"))

# ---------- 资讯页（DeepSeek 半价时段每天编三期 + 长桥行情） ----------
from newsroom import NewsRoom  # noqa: E402
NEWS = NewsRoom(dict({"base_url": CONFIG.get("deepseek", {}).get("base_url", "https://api.deepseek.com")},
                     **CONFIG.get("news", {})), deepseek_key)
NEWS.start()
CHAT_STOP = threading.Event()


def lan_urls():
    urls = []
    code, out, _ = run(["scutil", "--get", "LocalHostName"], timeout=3)
    if code == 0 and out.strip():
        urls.append("http://%s.local:%d/dash?k=%s" % (out.strip(), PORT, PAIR_KEY))
    for ifc in ("en0", "en1"):
        code, out, _ = run(["ipconfig", "getifaddr", ifc], timeout=3)
        if code == 0 and out.strip():
            urls.append("http://%s:%d/dash?k=%s" % (out.strip(), PORT, PAIR_KEY))
    return urls


def set_lan(enabled):
    """改 config.json 的 dashboard.lan，然后原地重启服务让监听地址生效。"""
    path = os.path.join(HERE, "config.json")
    cfg = load_json("config.json", None) or load_json("config.example.json", {})
    cfg.setdefault("dashboard", {})["lan"] = bool(enabled)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    def restart():
        time.sleep(0.6)
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), "--no-open"])
    threading.Thread(target=restart, daemon=True).start()


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    timeout = 60  # 连上不说话的连接 60 秒后断开，不让它一直占着线程

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        """本机访问：连接来自回环地址，且 Host 头是 127.0.0.1/localhost（挡 DNS 重绑定）。"""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return self.client_address[0] in LOCAL_ADDRS and host in ("127.0.0.1", "localhost")

    def _paired(self):
        """局域网访问监控台：开启了 LAN，且带对配对密钥（链接里的 ?k= 或之后的 Cookie）。"""
        if not LAN:
            return False
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("k", [""])[0]
        c = ""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, val = part.strip().partition("=")
            if name == "mb_k":
                c = val
        return any(v and hmac.compare_digest(v, PAIR_KEY) for v in (q, c))

    def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=False):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", "mb_k=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % PAIR_KEY)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dash_get(self, path):
        local = self._host_ok()
        if not local and not self._paired():
            msg = "这台设备还没配对。请在 Mac 上打开监控台 → 设置 → iPad 访问，用那里的链接打开。"
            return self._send(403, ("<meta name=viewport content='width=device-width'><body style='font:17px -apple-system;"
                                    "padding:40px;background:#0b0d12;color:#e8eaf0'>%s</body>" % msg).encode(),
                              "text/html; charset=utf-8")
        if path == "/dash":
            with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as f:
                html = f.read()
            boot = {"local": local, "token": TOKEN if local else "", "lan": LAN,
                    "pair_urls": lan_urls() if local and LAN else [],
                    "voice": os.path.exists(os.path.join(HERE, "static", "deck-voice.js")),
                    "aname": _assistant_name()}
            html = html.replace("/*__BOOT__*/{}", json.dumps(boot))
            return self._send(200, html.encode(), "text/html; charset=utf-8", cookie=not local)
        if path == "/api/dash":
            return self._send(200, HUB.snapshot())
        if path == "/api/dash/github/repo":
            # 点开某个仓库时才查提交、访客来源、PR（只读；仓库名必须在自己的仓库列表里）
            src = HUB.sources.get("github")
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if not src:
                return self._send(200, {"error": "GitHub 卡没开"})
            try:
                return self._send(200, src.detail((q.get("repo") or [""])[0]))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(200, {"error": "读仓库详情出错了：%s" % e})
        if path == "/news":
            with open(os.path.join(HERE, "news.html"), encoding="utf-8") as f:
                html = f.read()
            html = html.replace("/*__BOOT__*/{}", json.dumps({"local": local, "token": TOKEN if local else ""}))
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if path == "/api/news":
            return self._send(200, NEWS.snapshot())
        if path == "/api/news/market":
            return self._send(200, NEWS.market_status())
        if path in ("/dash-icon.png", "/dash.webmanifest"):
            fn = os.path.join(HERE, "static", path.lstrip("/"))
            with open(fn, "rb") as f:
                return self._send(200, f.read(), "image/png" if fn.endswith(".png") else "application/manifest+json")
        if path in STATIC_FILES:
            fn = os.path.join(HERE, "static", *path.split("/")[2:])  # 白名单里的路径，含 static/sfx/ 子目录
            if not os.path.exists(fn):
                return self._send(404, {"error": "not found"})
            with open(fn, "rb") as f:
                return self._send(200, f.read(), STATIC_FILES[path])
        return self._send(404, {"error": "not found"})

    def _chat(self, payload):
        """Vesper 对话：边生成边用 SSE 推给页面。同一时间只跑一个。"""
        if not FRIDAY.busy.acquire(blocking=False):
            return self._send(409, {"error": "上一个问题还在回答"})
        CHAT_STOP.clear()
        self.close_connection = True
        ok = [True]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(ev):
                if not ok[0]:
                    return
                try:
                    self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode())
                    self.wfile.flush()
                except OSError:
                    ok[0] = False

            ui = payload.get("ui") if isinstance(payload.get("ui"), dict) else {}
            FRIDAY.chat(str(payload.get("text") or ""), ui, emit, lambda: ok[0] and not CHAT_STOP.is_set())
        finally:
            FRIDAY.busy.release()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/dash", "/api/dash", "/api/dash/github/repo", "/dash-icon.png", "/dash.webmanifest",
                    "/news", "/api/news", "/api/news/market") or path in STATIC_FILES:
            return self._dash_get(path)
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                html = f.read().replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self._send(200, collect(deep=False))
        if path == "/api/history":
            if os.path.exists(HISTORY):
                with open(HISTORY, encoding="utf-8") as f:
                    return self._send(200, json.load(f))
            return self._send(200, [])
        if path == "/api/health":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > 65536:
            # 长度写错（负数、非数字）或太大：直接拒绝，不读请求体（负数会让 read 一直等到断开）
            self.close_connection = True
            return self._send(413 if n > 65536 else 400, {"error": "请求体长度不对"})
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + (self.headers.get("Host") or ""):
            # 只收自己页面发来的写请求（同源）；别的网站、沙盒 iframe（Origin: null）一律拒绝
            return self._send(403, {"error": "forbidden"})
        local = self._host_ok() and hmac.compare_digest(self.headers.get("X-Token") or "", TOKEN)
        if path == "/api/dash/refresh":
            # iPad 也能点刷新（只是让后台早点重新采集，不花钱）；需要配对 Cookie + 自定义头挡跨站请求
            if not (local or (self._paired() and self.headers.get("X-Dash") == "1")):
                return self._send(403, {"error": "forbidden"})
            src = payload.get("source")
            return self._send(200, {"accepted": HUB.refresh(src if isinstance(src, str) else None)})
        if not local:
            return self._send(403, {"error": "forbidden"})
        if path == "/api/sfx":
            # 扩展屏模式的按键音：交给 iPad 上的「Deck 音箱」放；没连上就回 ok:false，网页改在 Mac 上放
            return self._send(200, {"ok": ipad_sfx.send(payload.get("n"), payload.get("g", 0.75))})
        if path == "/api/news/refresh":
            # 手动重编一期要花 DeepSeek 的钱：只允许本机，且仍受每日预算限制
            threading.Thread(target=NEWS.generate, kwargs={"manual": True}, daemon=True).start()
            return self._send(200, {"ok": True})
        if path == "/api/news/market/connect":
            return self._send(200, NEWS.connect())
        if path == "/api/dash/lan":
            set_lan(payload.get("enabled"))
            return self._send(200, {"ok": True, "restarting": True})
        if path == "/api/deep":
            return self._send(200, collect(deep=True))
        if path == "/api/deepseek":
            return self._send(200, deepseek_check())
        if path == "/api/action":
            return self._send(200, do_action(payload.get("id"), payload.get("action")))
        if path == "/api/chat":
            return self._chat(payload)
        if path == "/api/chat/stop":
            CHAT_STOP.set()
            threading.Thread(target=HANDS.stop, daemon=True).start()
            return self._send(200, {"ok": True})
        if path == "/api/chat/history":
            return self._send(200, {"messages": FRIDAY.history(), "memories": FRIDAY.memories(), "cleanup": FRIDAY.cleanup_state()})
        if path == "/api/hands/confirm":
            # 页面确认卡上点了「做」：不经过 DeepSeek，直接执行那一步（和清理的按钮一样）
            if not FRIDAY.busy.acquire(blocking=False):
                return self._send(409, {"error": "Vesper 还在忙上一个问题"})
            try:
                CHAT_STOP.clear()
                HANDS.arm()
                evs = []
                res = HANDS.confirm(str(payload.get("id", "")), lambda: not CHAT_STOP.is_set(), evs.append)
            finally:
                FRIDAY.busy.release()
            return self._send(200, dict(res, events=[e for e in evs if e.get("t") != "acting"]))
        if path == "/api/hands/cancel":
            return self._send(200, HANDS.cancel(str(payload.get("id", ""))))
        if path == "/api/hands/status":
            return self._send(200, HANDS.status())
        if path == "/api/chat/new":
            return self._send(200, {"session": FRIDAY.new_session()})
        if path == "/api/proc/list":
            # 点 CPU/内存/GPU 圆环后的进程详情（和活动监视器同源，不打开活动监视器）
            return self._send(200, procs.top_by(str(payload.get("kind", "cpu")), 40))
        if path.startswith("/api/disk/"):
            # 点磁盘圆环后的「谁在占空间」（只读文件信息，不读内容，不出本机）
            op, vid = path[len("/api/disk/"):], str(payload.get("vol") or "mac")[:40]
            try:
                if op == "overview":
                    return self._send(200, diskwatch.overview(vid))
                if op == "grow":
                    win = {"hour": 3600, "day": 86400, "week": 7 * 86400}.get(payload.get("win"), 86400)
                    return self._send(200, diskwatch.growth(vid, win))
                if op == "recent":
                    return self._send(200, diskwatch.recent(vid))
                if op == "writers":
                    return self._send(200, diskwatch.WRITERS.top(0 if payload.get("win") == "boot" else 600))
                if op == "reveal":
                    return self._send(200, diskwatch.reveal(payload.get("path")))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(200, {"error": "读取出错了：%s" % type(e).__name__})
            return self._send(404, {"error": "not found"})
        if path == "/api/proc/kill":
            try:
                return self._send(200, procs.kill(int(payload.get("pid")), str(payload.get("name", "")), bool(payload.get("force"))))
            except (TypeError, ValueError):
                return self._send(400, {"error": "参数不对"})
        if path == "/api/proc/advise":
            kind = str(payload.get("kind", "cpu"))
            hit = ADVICE.get(kind)
            if hit and time.time() - hit[0] < 60 and (not payload.get("force") or time.time() - hit[0] < 10):
                return self._send(200, hit[1])
            out = procs.advise(kind, deepseek_key(), CONFIG.get("deepseek", {}), FRIDAY._usage_add)
            if not out.get("error"):
                ADVICE[kind] = (time.time(), out)
            return self._send(200, out)
        if path == "/api/friday/usage":
            return self._send(200, FRIDAY.usage_stats())
        if path == "/api/cleanup/scan":
            # 只扫描、不动文件。announce=true 时（每晚 23:30）把提醒记进聊天记录，之后口头说「好」Vesper 就知道在说哪次
            if payload.get("announce") and daily_clean.state().get("auto"):
                return self._send(200, {"auto": True, "rules": []})  # 自动模式：23:30 由服务器自己清，页面不用再问
            plan = FRIDAY.cleanup_scan()
            pub = cleaner_public(plan)
            if payload.get("announce") and plan["rules"]:
                pub["say"] = FRIDAY.announce_cleanup(plan)
            return self._send(200, pub)
        if path == "/api/cleanup/run":
            # 只有页面上点了「移到废纸篓」才会来到这里（本机 + 令牌）
            rules = payload.get("rules")
            if payload.get("cancel"):
                return self._send(200, FRIDAY.cleanup_cancel(str(payload.get("id", ""))))
            return self._send(200, FRIDAY.cleanup_run(str(payload.get("id", "")), rules if isinstance(rules, list) else None))
        if path == "/api/cleanup/auto":
            return self._send(200, daily_clean.set_auto(payload.get("on")))
        if path == "/api/cleanup/undo":
            return self._send(200, FRIDAY.cleanup_undo(str(payload.get("run_id", ""))))
        if path.startswith("/api/tools/"):
            try:
                return self._send(200, tools_api(path[len("/api/tools/"):], payload))
            except Exception as e:  # noqa: BLE001  工具箱出错不能拖垮整个服务
                traceback.print_exc()
                return self._send(200, {"error": "工具箱出错了：%s" % e})
        if path == "/api/memory/delete":
            old = FRIDAY.mem_delete(payload.get("id", 0))
            return self._send(200, {"ok": bool(old), "text": old and old[0], "kind": old and old[1], "created": old and old[2]})
        if path == "/api/memory/restore":
            FRIDAY.mem_restore(payload.get("id", 0), payload.get("text"), payload.get("kind"), payload.get("created"))
            return self._send(200, {"ok": True})
        if path == "/api/memory/update":
            return self._send(200, {"ok": FRIDAY.mem_update(payload.get("id", 0), payload.get("text")) is not None})
        return self._send(404, {"error": "not found"})


def main():
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("0.0.0.0" if LAN else "127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:%d/" % PORT
    if "--dash" in sys.argv:
        url += "dash"
    print("Mac 管家已启动：" + url + ("（监控台已对局域网开放）" if LAN else ""), flush=True)
    if "--no-open" not in sys.argv:
        threading.Timer(0.5, lambda: run(["open", url])).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
