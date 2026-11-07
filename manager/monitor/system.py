"""Mac 实时状态：CPU / 内存 / GPU / 磁盘 / 电池 / 网络 / 占用最高的进程。全部本机读取，不联网。"""
import ctypes
import os
import re
import time
from collections import deque

from .util import run

HISTORY_LEN = 90  # 2 秒一点 ≈ 3 分钟


class _CPU:
    def __init__(self):
        self.prev = None
        try:
            self.lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            self.lib.mach_host_self.restype = ctypes.c_uint32
        except OSError:
            self.lib = None

    def percent(self):
        """HOST_CPU_LOAD_INFO 的两次差值；第一次调用返回 None。"""
        if not self.lib:
            return None
        ticks = (ctypes.c_uint32 * 4)()
        count = ctypes.c_uint32(4)
        if self.lib.host_statistics(self.lib.mach_host_self(), 3, ticks, ctypes.byref(count)) != 0:
            return None
        cur = list(ticks)  # user, system, idle, nice
        prev, self.prev = self.prev, cur
        if not prev:
            return None
        d = [c - p for c, p in zip(cur, prev)]
        total = sum(d)
        if total <= 0:
            return None
        return round(100.0 * (total - d[2]) / total, 1)


def _sysctl(name):
    code, out, _ = run(["sysctl", "-n", name], timeout=3)
    return out.strip() if code == 0 else ""


def _memory():
    total = int(_sysctl("hw.memsize") or 0)
    code, vm, _ = run(["vm_stat"], timeout=3)
    page = 16384
    m = re.search(r"page size of (\d+)", vm)
    if m:
        page = int(m.group(1))
    pages = {k.strip(): int(v) for k, v in re.findall(r"^([^:]+):\s+(\d+)\.?$", vm, re.M)}
    # 与“活动监视器 · 已使用内存”口径一致：App 内存 + 联动 + 压缩
    app = max(0, pages.get("Anonymous pages", pages.get("Pages active", 0)) - pages.get("Pages purgeable", 0))
    used = (app + pages.get("Pages wired down", 0) + pages.get("Pages occupied by compressor", 0)) * page
    swap = None
    s = _sysctl("vm.swapusage")
    m = re.search(r"used = ([\d.]+)M", s)
    if m:
        swap = round(float(m.group(1)) / 1024, 2)
    return {"total_gb": round(total / 2**30, 1), "used_gb": round(min(used, total) / 2**30, 1),
            "pct": round(100.0 * used / total, 1) if total else None, "swap_gb": swap}


def _gpu():
    code, out, _ = run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"], timeout=4)
    m = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}', out)
    if not m:
        return None
    stats = {k: int(v) for k, v in re.findall(r'"([^"]+)"=(\d+)', m.group(1))}
    cores = re.search(r'"gpu-core-count"\s*=\s*(\d+)', out)
    return {"pct": stats.get("Device Utilization %"),
            "mem_gb": round(stats["In use system memory"] / 2**30, 1) if "In use system memory" in stats else None,
            "cores": int(cores.group(1)) if cores else None}


def _disk():
    for p in ("/System/Volumes/Data", "/"):
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if total:
            d = {"total_gb": round(total / 1e9), "free_gb": round(free / 1e9, 1),
                 "pct": round(100.0 * (total - free) / total, 1)}
            try:  # 外置硬盘：插上自动出现（主页磁盘那栏多一行，选的方案 A）
                import diskwatch
                d["ext"] = [{"name": v["name"], "total_gb": round(v["total"] / 1e9), "free_gb": round(v["free"] / 1e9, 1),
                             "pct": v["pct"]} for v in diskwatch.volumes() if v["ext"]]
            except Exception:  # noqa: BLE001
                d["ext"] = []
            return d
    return None


def _battery():
    code, out, _ = run(["pmset", "-g", "batt"], timeout=3)
    m = re.search(r"(\d+)%;\s*([^;]+);\s*([^\n]*)", out)
    if not m:
        return None
    state = m.group(2).strip()
    remain = re.search(r"(\d+:\d+) remaining", m.group(3))
    return {"pct": int(m.group(1)), "charging": state in ("charging", "charged", "finishing charge"),
            "on_ac": "AC Power" in out, "state": state, "remaining": remain.group(1) if remain else None}


def _net_bytes():
    code, out, _ = run(["netstat", "-ibn"], timeout=3)
    rx = tx = 0
    seen = set()
    for line in out.splitlines()[1:]:
        p = line.split()
        if len(p) < 10 or not p[2].startswith("<Link#") or p[0].startswith(("lo", "utun", "awdl", "llw", "bridge", "gif", "stf", "anpi", "ap")):
            continue
        if p[0] in seen:
            continue
        seen.add(p[0])
        # 列：Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll；没有 MAC 的接口少一列
        i, o = (6, 9) if len(p) >= 11 else (5, 8)
        try:
            rx += int(p[i])
            tx += int(p[o])
        except (ValueError, IndexError):
            continue
    return rx, tx


def _top_procs(n=4):
    code, out, _ = run(["ps", "-Aceo", "pcpu=,rss=,comm=", "-r"], timeout=3)
    res = []
    for line in out.splitlines():
        p = line.split(None, 2)
        if len(p) < 3:
            continue
        try:
            res.append({"name": p[2][:40], "cpu": float(p[0]), "mem_mb": round(int(p[1]) / 1024)})
        except ValueError:
            continue
        if len(res) >= n:
            break
    return res


class SystemSource:
    key = "system"
    title = "Mac 实时状态"
    default_interval = 2

    def __init__(self, cfg):
        self.cpu = _CPU()
        self.cpu.percent()
        self.hist = {k: deque(maxlen=HISTORY_LEN) for k in ("cpu", "mem", "gpu", "rx", "tx")}
        self.prev_net = None
        self.model = None
        self.slow = {}
        self.slow_at = 0

    def collect(self, force=False):
        now = time.time()
        cpu = self.cpu.percent()
        mem = _memory()
        gpu = _gpu()
        rx, tx = _net_bytes()
        rate = None
        if self.prev_net and now > self.prev_net[0]:
            dt = now - self.prev_net[0]
            rate = (max(0, rx - self.prev_net[1]) / dt, max(0, tx - self.prev_net[2]) / dt)
        self.prev_net = (now, rx, tx)
        # 慢变化的信息 30 秒读一次
        if force or now - self.slow_at > 30:
            boot = re.search(r"sec = (\d+)", _sysctl("kern.boottime"))
            self.slow = {"disk": _disk(), "battery": _battery(),
                         "boot": int(boot.group(1)) if boot else None, "procs": _top_procs()}
            self.slow_at = now
        elif now - self.slow_at > 6:
            self.slow["procs"] = _top_procs()
        if self.model is None:
            self.model = {"chip": _sysctl("machdep.cpu.brand_string"), "name": _sysctl("hw.model"),
                          "p": _sysctl("hw.perflevel0.physicalcpu"), "e": _sysctl("hw.perflevel1.physicalcpu")}
        for k, v in (("cpu", cpu), ("mem", mem.get("pct")), ("gpu", gpu and gpu.get("pct")),
                     ("rx", rate and rate[0]), ("tx", rate and rate[1])):
            if v is not None:
                self.hist[k].append(round(v, 1))
        pcts = [v for v in (cpu, mem.get("pct"), self.slow.get("disk") and self.slow["disk"]["pct"]) if v is not None]
        level = "error" if any(p >= 95 for p in pcts) else "warn" if any(p >= 85 for p in pcts) else "ok"
        why = []
        for nm, v, fx in (("CPU", cpu, "点 CPU 圆环看是谁在占"), ("内存", mem.get("pct"), "点内存圆环看是谁在占"),
                          ("磁盘", self.slow.get("disk") and self.slow["disk"]["pct"], "让 Vesper 扫一下能清的东西")):
            if v is not None and v >= 85:
                # 和圆环一样四舍五入（圆环 88、这里却写 87% 的问题）；中文名后面不空格，英文（CPU）后面空一格
                why.append({"text": "%s%s用了 %d%%" % (nm, " " if nm.isascii() else "", round(v)), "fix": fx})
        try:
            import daily_clean
            sysd = daily_clean.state().get("sysdata") or {}
        except Exception:  # noqa: BLE001
            sysd = {}
        if sysd.get("surge"):
            level = "warn" if level == "ok" else level
            why.append({"text": "系统数据暴涨：" + "、".join(sysd["surge"]),
                        "fix": "已自动清过，清空废纸篓才会腾出空间" if sysd.get("last_surge") else "打开每日清理的自动清，或清空废纸篓"})
        if sysd.get("codex_warn"):
            level = "warn" if level == "ok" else level
            why.append({"text": "Codex 插件市场暂存又在涨", "fix": "这项要先问你，不会自动清"})
        return {
            "level": level, "why": why, "sysdata": sysd,
            "cpu": cpu, "memory": mem, "gpu": gpu,
            "net": {"rx_bps": round(rate[0]) if rate else None, "tx_bps": round(rate[1]) if rate else None},
            "disk": self.slow.get("disk"), "battery": self.slow.get("battery"),
            "boot": self.slow.get("boot"), "procs": self.slow.get("procs", []),
            "model": self.model,
            "history": {k: list(v) for k, v in self.hist.items()},
        }
