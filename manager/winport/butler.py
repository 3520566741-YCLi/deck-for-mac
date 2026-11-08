"""管家页（我的应用）Windows 版：和 server.py 里 Mac 版 collect() / do_action() 同样的返回格式。

Mac → Windows 对照：
- 应用登记表 apps.json → %APPDATA%\\Deck\\apps.json（没有就用 apps.windows.example.json，只登记 Deck 自己）；
  kind: app = 一个 exe；folder = 一个文件夹里的散装程序；agent = 「任务计划程序」里的任务（相当于 LaunchAgent）。
- ps → psutil；「测显存」→ 每个进程的 GPU 专用显存（PDH 计数器 GPU Process Memory）。
- Dock 失效图标 → 任务栏固定的快捷方式指向的程序已经不存在。
- DiagnosticReports 崩溃报告 → Windows 错误报告（WER）ReportArchive 里的 AppCrash_*。
- 打开 = 系统默认方式启动；退出 = 给它的窗口发「关闭」（和点右上角 × 一样，会提示保存）；在资源管理器中显示。
只读系统信息；动作只针对登记表里的应用，路径和任务名都来自登记表，不接受网页传来的路径。
"""
import ctypes
import glob
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import psutil

import deckos

IS_WIN = sys.platform == "win32"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW：调 schtasks / PowerShell 时不闪黑框

SYSTEM_PROMPT = """你是 Windows 电脑上自制应用的运维排查助手。用户给你一份本机快照（JSON），包含每个自制应用的
安装/运行状态、内存、CPU、计划任务状态、崩溃摘要（Windows 错误报告）、任务栏失效快捷方式。逐个应用判断它是否健康、
哪里有问题、最可能的原因、用户下一步该做的最小动作（给具体的设置路径或 PowerShell 命令）。不要编造快照里没有的数据。
只输出 JSON：{"summary": "一句话总评", "apps": [{"id": "...", "level": "ok|warn|error",
"finding": "发现（中文，≤60字）", "fix": "建议（中文，≤80字，没问题写“无需处理”）"}],
"dock": "任务栏失效快捷方式的处理建议（没有就写“无”）"}"""


def x(path):
    return os.path.expandvars(os.path.expanduser(path)) if path else path


def registry():
    """应用登记表：用户自己的在 %APPDATA%\\Deck\\apps.json，没有就用示例（只有 Deck 自己）。"""
    for p in (deckos.support("Deck", "apps.json"), os.path.join(HERE, "apps.windows.example.json")):
        try:
            with open(p, encoding="utf-8") as f:
                apps = json.load(f).get("apps", [])
            return [a for a in apps if isinstance(a, dict) and a.get("id")]
        except (OSError, ValueError):
            continue
    return []


# ---------------------------------------------------------------- 进程

def _rows():
    """[{pid, ppid, cpu, rss_mb, etime, cmd}]，cmd 是可执行文件完整路径（和 Mac 版 ps_table 一样）。CPU 用 procs 模块的快照。"""
    cpu = {}
    try:
        import procs  # Windows 上是 winport.procs，2 秒缓存，和进程页共用一次采样
        cpu = {r["pid"]: r["cpu"] for r in procs.snapshot(max_age=5.0)["rows"]}
    except Exception:  # noqa: BLE001  CPU 读不到就当 0，不影响其他信息
        pass
    rows, now = [], time.time()
    for p in psutil.process_iter(["pid", "ppid", "exe", "name", "memory_info", "create_time"]):
        i = p.info
        try:
            mi = i.get("memory_info")
            rows.append({"pid": i["pid"], "ppid": i.get("ppid") or 0, "cpu": cpu.get(i["pid"], 0.0),
                         "rss_mb": round(mi.rss / 1048576, 1) if mi else 0.0,
                         "etime": _etime(now - (i.get("create_time") or now)),
                         "cmd": i.get("exe") or i.get("name") or ""})
        except (TypeError, ValueError):
            continue
    return rows


def _etime(sec):
    sec = int(max(0, sec))
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return ("%d-%02d:%02d:%02d" % (d, h, m, s)) if d else ("%02d:%02d:%02d" % (h, m, s)) if h else "%02d:%02d" % (m, s)


def descendants(rows, roots):
    kids, frontier = set(roots), list(roots)
    by_parent = {}
    for r in rows:
        by_parent.setdefault(r["ppid"], []).append(r["pid"])
    while frontier:
        for c in by_parent.get(frontier.pop(), []):
            if c not in kids:
                kids.add(c)
                frontier.append(c)
    return kids


def _match(rule, cmd):
    """match 片段不分大小写、斜杠方向都行（Windows 路径不分大小写）。"""
    if not rule or not cmd:
        return False
    return rule.replace("/", "\\").lower() in cmd.replace("/", "\\").lower()


# ---------------------------------------------------------------- 系统

def memory_stats():
    vm = psutil.virtual_memory()
    used = vm.total - vm.available
    return {"total_gb": round(vm.total / 2**30, 1), "used_gb": round(used / 2**30, 1),
            "free_pct": int(round(100.0 * vm.available / vm.total)) if vm.total else None}


_gpu = [None]


def gpu_stats():
    """GPU 使用率 + 独显已用显存（和主页「电脑实时状态」同一个读数）。"""
    try:
        if _gpu[0] is None:
            from monitor.system_win import _GPU
            _gpu[0] = _GPU()
            _gpu[0].read()
            time.sleep(0.3)  # PDH 要两次采样
        g = _gpu[0].read() or {}
    except Exception:  # noqa: BLE001
        return {}
    res = {}
    if g.get("pct") is not None:
        res["utilization_pct"] = g["pct"]
    if g.get("mem_gb") is not None:
        res["in_use_mb"] = int(round(g["mem_gb"] * 1024))
    return res


def gpu_mem_by_pid():
    """{pid: 专用显存 MB}，PDH「GPU Process Memory(pid_N_*)\\Dedicated Usage」。读不到返回 {}。「测显存」时才调用。"""
    if not IS_WIN:
        return {}
    try:
        from monitor.system_win import PDH_FMT_LARGE, PDH_MORE_DATA, _PdhItem
    except ImportError:
        return {}
    pdh = ctypes.WinDLL("pdh")
    q, c = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
        return {}
    try:
        add = pdh.PdhAddEnglishCounterW
        add.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        if add(q, r"\GPU Process Memory(*)\Dedicated Usage", None, ctypes.byref(c)) != 0:
            return {}
        if pdh.PdhCollectQueryData(q) != 0:
            return {}
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        if (pdh.PdhGetFormattedCounterArrayW(c, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None)
                & 0xFFFFFFFF) != PDH_MORE_DATA:
            return {}
        buf = (ctypes.c_byte * size.value)()
        if pdh.PdhGetFormattedCounterArrayW(c, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_PdhItem))
        res = {}
        for i in range(count.value):
            m = re.match(r"pid_(\d+)_", items[i].szName or "")
            if m and items[i].FmtValue.CStatus in (0, 1):
                pid = int(m.group(1))
                res[pid] = res.get(pid, 0) + max(0, items[i].FmtValue.u.large)
        return {k: v / 1048576 for k, v in res.items()}
    except OSError:
        return {}
    finally:
        pdh.PdhCloseQuery(q)


# ---------------------------------------------------------------- exe 版本号

def exe_version(path):
    """exe 的「产品版本」（文件属性 → 详细信息里那个）。读不到返回 None。"""
    if not IS_WIN:
        return None
    try:
        ver = ctypes.windll.version
        n = ver.GetFileVersionInfoSizeW(path, None)
        if not n:
            return None
        buf = ctypes.create_string_buffer(n)
        if not ver.GetFileVersionInfoW(path, 0, n, buf):
            return None
        p, ln = ctypes.c_void_p(), ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(ln)) or ln.value < 52:
            return None
        ms, ls = struct.unpack_from("<II", ctypes.string_at(p.value, 52), 16)  # dwProductVersionMS / LS
        parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
        while len(parts) > 2 and parts[-1] == 0:
            parts.pop()
        return ".".join(map(str, parts))
    except (OSError, AttributeError, struct.error):
        return None


# ---------------------------------------------------------------- 计划任务（相当于 LaunchAgent）

_tasks = {"at": 0.0, "data": []}
_tasks_lock = threading.Lock()

# 只看根目录「\」下的任务：自己建的任务基本都在这；\Microsoft\ 下面是系统自带的。
# 用 PowerShell 是因为 schtasks 的输出列名会跟着系统语言变，不好解析；Get-ScheduledTask 的字段名是固定英文。
_PS_TASKS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "@(Get-ScheduledTask -TaskPath '\\' | ForEach-Object {"
    " $i = $_ | Get-ScheduledTaskInfo; $a = @($_.Actions)[0];"
    " [pscustomobject]@{label=$_.TaskName; state=[string]$_.State; last=$i.LastTaskResult;"
    " runs=$i.NumberOfMissedRuns; program=[string]$a.Execute; author=[string]$_.Author} }) | ConvertTo-Json -Compress"
)
VENDOR = ("MicrosoftEdgeUpdate", "GoogleUpdate", "OneDrive", "Adobe", "NvTm", "NVIDIA", "Intel", "Dropbox", "CCleaner",
          "Opera", "Brave", "Zoom", "npcapwatchdog", "AMD", "Lenovo", "Dell", "HP", "ASUS")


def scheduled_tasks(max_age=60):
    """根目录下的计划任务，60 秒缓存（PowerShell 起一次要一秒左右）。"""
    if not IS_WIN:
        return []
    with _tasks_lock:
        if time.time() - _tasks["at"] < max_age:
            return _tasks["data"]
        data = []
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_TASKS],  # noqa: S603,S607
                               capture_output=True, timeout=20, creationflags=NO_WINDOW)
            raw = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
            for t in raw if isinstance(raw, list) else [raw]:
                prog = x((t.get("program") or "").strip('"'))
                last = t.get("last")
                data.append({"label": t.get("label") or "", "loaded": (t.get("state") or "") != "Disabled",
                             "state": t.get("state") or "", "pid": None,
                             "last_exit": None if last in (None, 267011) else ("0" if last == 0 else "0x%X" % (last & 0xFFFFFFFF)),
                             "runs": None, "program": prog,
                             "program_exists": bool(prog) and (os.path.exists(prog) or "\\" not in prog),
                             "running": (t.get("state") or "") == "Running"})
        except (OSError, ValueError, subprocess.SubprocessError):
            data = _tasks["data"]
        _tasks.update(at=time.time(), data=data)
        return data


# ---------------------------------------------------------------- 任务栏（相当于 Dock）

TASKBAR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")


def lnk_target(path):
    """读 .lnk 快捷方式指向的本地路径（只解析文件里的 LinkInfo，不调 COM）。读不到返回 ""。"""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
        if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return ""
        flags = struct.unpack_from("<I", data, 20)[0]
        off = 76
        if flags & 0x1:  # HasLinkTargetIDList
            off += 2 + struct.unpack_from("<H", data, off)[0]
        if not flags & 0x2:  # 没有 LinkInfo（比如指向 UWP 应用、控制面板）
            return ""
        li = off
        hdr = struct.unpack_from("<I", data, li + 4)[0]
        li_flags = struct.unpack_from("<I", data, li + 8)[0]
        if not li_flags & 0x1:  # VolumeIDAndLocalBasePath
            return ""
        if hdr >= 0x24:  # 有 Unicode 版本
            uoff = struct.unpack_from("<I", data, li + 28)[0]
            raw = data[li + uoff:li + uoff + 1040]
            return raw.decode("utf-16-le", "ignore").split("\x00", 1)[0]
        boff = struct.unpack_from("<I", data, li + 16)[0]
        return data[li + boff:li + boff + 520].split(b"\x00", 1)[0].decode("mbcs" if IS_WIN else "latin-1", "ignore")
    except (OSError, struct.error):
        return ""


def dock_items():
    res = []
    for p in sorted(glob.glob(os.path.join(TASKBAR, "*.lnk"))):
        target = lnk_target(p)
        res.append({"section": "taskbar", "label": os.path.splitext(os.path.basename(p))[0], "path": target,
                    "exists": (not target) or os.path.exists(target)})
    return res


# ---------------------------------------------------------------- 崩溃（Windows 错误报告）

def _wer_dirs():
    local = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    return [os.path.join(local, r"Microsoft\Windows\WER\ReportArchive"), os.path.join(local, r"Microsoft\Windows\WER\ReportQueue"),
            os.path.join(pd, r"Microsoft\Windows\WER\ReportArchive"), os.path.join(pd, r"Microsoft\Windows\WER\ReportQueue")]


def recent_crashes(proc, days=7):
    """近 days 天这个程序的崩溃报告。WER 的文件夹名是 AppCrash_<exe 名>_<哈希>…；proc 写 exe 名（可带可不带 .exe）。"""
    if not proc:
        return []
    want = proc.lower()
    want = want if want.endswith(".exe") else want + ".exe"
    cutoff, out = time.time() - days * 86400, []
    for d in _wer_dirs():
        for p in glob.glob(os.path.join(d, "AppCrash_*")) + glob.glob(os.path.join(d, "AppHang_*")):
            name = os.path.basename(p).lower()
            exe = name.split("_", 2)[1] if name.count("_") >= 2 else ""
            if not (exe == want or (len(exe) >= 30 and want.startswith(exe))):  # WER 会把长名字截断
                continue
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt >= cutoff:
                rep = os.path.join(p, "Report.wer")
                out.append({"file": rep if os.path.exists(rep) else p,
                            "time": time.strftime("%m-%d %H:%M", time.localtime(mt)),
                            "kind": "卡死" if name.startswith("apphang") else "崩溃"})
    return sorted(out, key=lambda c: c["time"], reverse=True)[:5]


def crash_excerpt(path, limit=1200):
    """从 Report.wer 里挑出关键几行：出错模块、异常代码、偏移。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(200000)
    except OSError:
        return ""
    txt = raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    keep = [ln.strip() for ln in txt.splitlines()
            if re.match(r"\s*(EventType|FriendlyEventName|AppName|AppPath|Sig\[\d+\]\.(Name|Value)|"
                        r"DynamicSig\[\d+\]\.Value|ConsentKey)=", ln)]
    return "\n".join(keep)[:limit]


def url_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310  登记表里写的本机服务地址
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 汇总

def collect(deep=False):
    reg = registry()
    rows = _rows()
    tasks = scheduled_tasks()
    task_by_label = {t["label"].lower(): t for t in tasks}
    dock = dock_items()
    dock_paths = {os.path.normcase(d["path"]) for d in dock if d["path"]}
    gmem = gpu_mem_by_pid() if deep else {}
    apps = []
    for a in reg:
        item, issues, pids = dict(a), [], []
        if a.get("self"):  # Deck 自己：装在哪、从源码跑还是装好的 exe 都认
            path = item["path"] = _self_path()
            item["installed"], item["in_dock"] = True, os.path.normcase(path) in dock_paths
            item["version"] = deckos_version()
            pids = sorted(descendants(rows, [_self_root()]))
        elif a.get("kind") in ("app", "folder"):
            path = x(a.get("path", ""))
            item["installed"] = bool(path) and os.path.exists(path)
            if not item["installed"]:
                issues.append("没装：%s 不存在" % path)
            elif path.lower().endswith(".exe"):
                item["version"] = exe_version(path)
                item["exe_ok"] = os.path.getsize(path) > 0
                if not item["exe_ok"]:
                    issues.append("exe 文件是空的（空壳）")
            item["in_dock"] = os.path.normcase(path) in dock_paths
            rule = a.get("match") or path
            roots = [r["pid"] for r in rows if _match(rule, r["cmd"])]
            pids = sorted(descendants(rows, roots))
        if a.get("kind") == "agent":
            t = task_by_label.get((a.get("label") or "").lower())
            item["agent"] = t
            if not t:
                issues.append("找不到计划任务 %s" % a.get("label"))
            else:
                if not t["loaded"]:
                    issues.append("计划任务被禁用了（没加载）")
                if t.get("last_exit") not in (None, "0"):
                    issues.append("上次退出码 %s" % t["last_exit"])
                if a.get("match"):
                    roots = [r["pid"] for r in rows if _match(a["match"], r["cmd"])]
                    pids = sorted(descendants(rows, roots))
        procs_ = [r for r in rows if r["pid"] in pids]
        item["running"] = bool(procs_)
        item["pids"] = pids
        item["cpu"] = round(sum(r["cpu"] for r in procs_), 1)
        item["rss_mb"] = round(sum(r["rss_mb"] for r in procs_), 1)
        item["uptime"] = max(procs_, key=lambda r: len(r["etime"]))["etime"] if procs_ else None
        if deep and procs_:
            g = [gmem[p] for p in pids if p in gmem]
            item["gpu_mb"] = round(sum(g), 1) if g else None
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
    tracked = {(a.get("label") or "").lower() for a in reg}
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "memory": memory_stats(),
        "gpu": gpu_stats(),
        "apps": apps,
        "dock": dock,
        "dock_broken": [d for d in dock if not d["exists"]],
        "other_agents": [t for t in tasks if t["label"].lower() not in tracked and not t["label"].startswith(VENDOR)],
    }


# ---------------------------------------------------------------- Deck 自己

def _self_path():
    return sys.executable if getattr(sys, "frozen", False) else os.path.join(HERE, "server.py")


def _self_root():
    """装好的 Deck.exe 是一个进程（WebView2 是它的子进程）；从源码跑时是 python（外壳 deck_win.py 是它自己）。"""
    return os.getpid()


def deckos_version():
    for p in (os.path.join(HERE, "..", "windows", "VERSION"),):  # 源码和安装包里都在 manager 旁边的 windows/
        try:
            with open(p, encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- 动作

def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)  # noqa: S603
        return r.returncode, r.stdout.decode("mbcs" if IS_WIN else "utf-8", "replace"), r.stderr.decode("mbcs" if IS_WIN else "utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def do_action(app_id, action):
    a = {r["id"]: r for r in registry()}.get(app_id)
    if not a:
        return {"error": "未知应用"}
    if not IS_WIN:
        return {"error": "只在 Windows 上能操作"}
    if a.get("kind") == "agent":
        if action != "restart":
            return {"error": "计划任务只支持重启"}
        tn = "\\" + (a.get("label") or "").lstrip("\\")
        _run(["schtasks.exe", "/End", "/TN", tn])  # 没在跑时会报错，不管
        code, out, err = _run(["schtasks.exe", "/Run", "/TN", tn])
        _tasks["at"] = 0
        return {"ok": code == 0, "msg": (err or out).strip()}
    path = _self_path() if a.get("self") else x(a.get("path", ""))
    if a.get("self") and action in ("open", "quit"):
        return {"error": "这就是 Deck 自己：它已经开着；要退出请用托盘图标里的「退出」"}
    if action == "open":
        if not os.path.exists(path):
            return {"error": "没找到 %s" % path}
        try:
            os.startfile(path)  # noqa: S606  只启动登记表里的程序
        except OSError as e:
            return {"ok": False, "msg": str(e)}
        return {"ok": True, "msg": ""}
    if action == "quit":
        import procs
        rows = _rows()
        roots = [r["pid"] for r in rows if _match(a.get("match") or path, r["cmd"])]
        sent = sum(procs._close_windows(pid) for pid in roots)
        if not roots:
            return {"ok": True, "msg": "它本来就没在运行"}
        if not sent:
            return {"ok": False, "msg": "它没有窗口，没法让它自己退出；要结束可以去「进程」里点强制结束"}
        return {"ok": True, "msg": "已让它关闭窗口（有没保存的东西它会先问你）"}
    if action == "reveal":
        if not os.path.exists(path):
            return {"error": "没找到 %s" % path}
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])  # noqa: S603,S607
        return {"ok": True, "msg": ""}
    return {"error": "未知动作"}
