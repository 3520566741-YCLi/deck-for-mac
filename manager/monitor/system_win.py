"""电脑实时状态（Windows 版）：CPU / 内存 / GPU / 磁盘 / 电池 / 网络 / 占用最高的进程。

和 system.py（Mac）返回同样结构的数据，页面不用改。全部本机读取，不联网。
- CPU、内存、磁盘、电池、网速、进程：psutil（BSD-3，跨平台）
- GPU 使用率、显存：Windows 性能计数器 PDH（和任务管理器同一个数据来源），读不到就不显示
在 Linux 上也能跑（开发和 CI 用），只是没有 GPU 和处理器型号。
"""
import ctypes
import os
import platform
import sys
import time
from collections import deque

import psutil

HISTORY_LEN = 90  # 2 秒一点 ≈ 3 分钟
IS_WIN = sys.platform == "win32"  # 真 Windows 才读注册表 / PDH；DECK_OS=win 模拟时这些跳过


# ---------- GPU：PDH 性能计数器 ----------

class _PdhValue(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("long", ctypes.c_long), ("double", ctypes.c_double), ("large", ctypes.c_longlong)]
    _fields_ = [("CStatus", ctypes.c_ulong), ("u", _U)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhValue)]


PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_LARGE = 0x00000400
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2


class _GPU:
    """3D 引擎使用率（所有进程相加，最多 100）+ 独显已用显存。第一次调用返回 None（PDH 要两次采样算差值）。"""

    def __init__(self):
        self.ok = False
        if not IS_WIN:
            return
        try:
            self.pdh = ctypes.WinDLL("pdh")
            self.query = ctypes.c_void_p()
            self.util = ctypes.c_void_p()
            self.mem = ctypes.c_void_p()
            if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
                return
            add = self.pdh.PdhAddEnglishCounterW
            add.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            if add(self.query, r"\GPU Engine(*engtype_3D)\Utilization Percentage", None, ctypes.byref(self.util)) != 0:
                return
            if add(self.query, r"\GPU Adapter Memory(*)\Dedicated Usage", None, ctypes.byref(self.mem)) != 0:
                self.mem = None
            self.pdh.PdhCollectQueryData(self.query)
            self.ok = True
        except (OSError, AttributeError):
            self.ok = False

    def _array(self, counter, fmt):
        get = self.pdh.PdhGetFormattedCounterArrayW
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        rc = get(counter, fmt, ctypes.byref(size), ctypes.byref(count), None) & 0xFFFFFFFF
        if rc != PDH_MORE_DATA or not size.value:
            return []
        buf = (ctypes.c_byte * size.value)()
        if get(counter, fmt, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(_PdhItem))
        return [items[i] for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1)]

    def read(self):
        if not self.ok:
            return None
        try:
            if self.pdh.PdhCollectQueryData(self.query) != 0:
                return None
            util = sum(i.FmtValue.u.double for i in self._array(self.util, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100))
            mem = None
            if self.mem:
                vals = [i.FmtValue.u.large for i in self._array(self.mem, PDH_FMT_LARGE)]
                mem = round(sum(vals) / 2**30, 1) if vals else None
            return {"pct": int(round(min(100.0, max(0.0, util)))), "mem_gb": mem, "cores": None}
        except (OSError, ValueError):
            return None


# ---------- 处理器 / 系统 ----------

def _cpu_name():
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or ""


def _os_name():
    if IS_WIN:
        v = sys.getwindowsversion()
        return "Windows 11" if v.major == 10 and v.build >= 22000 else "Windows %s" % platform.release()
    return "%s %s" % (platform.system(), platform.release())


def _volume_label(root):
    if not IS_WIN:
        return ""
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0)
    return buf.value if ok else ""


def _system_root():
    return (os.environ.get("SystemDrive", "C:") + "\\") if IS_WIN else "/"


def _memory():
    vm = psutil.virtual_memory()
    try:
        swap = round(psutil.swap_memory().used / 2**30, 2)
    except (OSError, RuntimeError):
        swap = None
    used = vm.total - vm.available  # 和任务管理器「使用中」口径接近
    return {"total_gb": round(vm.total / 2**30, 1), "used_gb": round(used / 2**30, 1),
            "pct": round(100.0 * used / vm.total, 1) if vm.total else None, "swap_gb": swap}


class _StorageDescriptor(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("Size", ctypes.c_ulong), ("DeviceType", ctypes.c_ubyte),
                ("DeviceTypeModifier", ctypes.c_ubyte), ("RemovableMedia", ctypes.c_ubyte), ("CommandQueueing", ctypes.c_ubyte),
                ("VendorIdOffset", ctypes.c_ulong), ("ProductIdOffset", ctypes.c_ulong), ("ProductRevisionOffset", ctypes.c_ulong),
                ("SerialNumberOffset", ctypes.c_ulong), ("BusType", ctypes.c_int), ("RawPropertiesLength", ctypes.c_ulong)]


# STORAGE_BUS_TYPE：1394 = 4，USB = 7，SD = 12，MMC = 13。这些接口上的盘才算「外置」
_EXT_BUS = {4, 7, 12, 13}


def _is_external_drive(mount):
    """问磁盘驱动这个盘挂在什么总线上（IOCTL_STORAGE_QUERY_PROPERTY，不要管理员权限）。
    USB 固态硬盘会被系统标成「固定盘」，光看 removable 会漏；第二块内置硬盘（比如 D 盘）又不该算外置。"""
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    path = "\\\\.\\" + mount.rstrip("\\")  # \\.\D:
    h = k32.CreateFileW(ctypes.c_wchar_p(path), 0, 0x3, None, 3, 0, None)  # 不要读写权限，共享读写，OPEN_EXISTING
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        query = (ctypes.c_ulong * 3)(0, 0, 0)  # StorageDeviceProperty, PropertyStandardQuery
        desc = _StorageDescriptor()
        got = ctypes.c_ulong(0)
        ok = k32.DeviceIoControl(ctypes.c_void_p(h), 0x2D1400, query, ctypes.sizeof(query), ctypes.byref(desc),
                                 ctypes.sizeof(desc), ctypes.byref(got), None)
        if not ok:
            return None
        return desc.BusType in _EXT_BUS or bool(desc.RemovableMedia)
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def external_volumes():
    """插着的移动硬盘 / U 盘。可移动盘直接算；固定盘再问一下总线，USB / 雷电外接 / SD 卡才算外置，
    第二块内置硬盘、云电脑的临时盘不算。"""
    res = []
    sysroot = _system_root().upper()
    for p in psutil.disk_partitions(all=False):
        opts = (p.opts or "").lower()
        if IS_WIN:
            if "cdrom" in opts or not p.fstype or p.mountpoint.upper() == sysroot:
                continue
            if "removable" in opts:
                ext = True
            elif "fixed" in opts:
                try:
                    ext = bool(_is_external_drive(p.mountpoint))
                except OSError:
                    ext = False
            else:
                ext = False
        else:
            ext = p.mountpoint.startswith(("/media/", "/run/media/"))
        if not ext:
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        if not u.total:
            continue
        label = _volume_label(p.mountpoint) or p.mountpoint.rstrip("\\/")
        name = ("%s（%s）" % (label, p.mountpoint.rstrip("\\"))) if IS_WIN and label != p.mountpoint.rstrip("\\") else label
        res.append({"name": name, "mount": p.mountpoint, "total": u.total, "free": u.free, "pct": round(u.percent, 1), "ext": True})
    return res


def _disk():
    try:
        u = psutil.disk_usage(_system_root())
    except OSError:
        return None
    d = {"total_gb": round(u.total / 1e9), "free_gb": round(u.free / 1e9, 1), "pct": round(u.percent, 1)}
    try:
        d["ext"] = [{"name": v["name"], "total_gb": round(v["total"] / 1e9), "free_gb": round(v["free"] / 1e9, 1), "pct": v["pct"]}
                    for v in external_volumes()]
    except Exception:  # noqa: BLE001
        d["ext"] = []
    return d


def _battery():
    try:
        b = psutil.sensors_battery()
    except (OSError, RuntimeError, AttributeError):
        return None
    if b is None:
        return None
    secs = b.secsleft
    remain = None
    if isinstance(secs, int) and secs > 0 and secs != psutil.POWER_TIME_UNLIMITED:
        remain = "%d:%02d" % (secs // 3600, secs % 3600 // 60)
    pct = int(round(b.percent))
    charging = bool(b.power_plugged) and pct < 100
    return {"pct": pct, "charging": charging, "on_ac": bool(b.power_plugged),
            "state": "charging" if charging else "charged" if b.power_plugged else "discharging", "remaining": remain}


def _net_bytes():
    rx = tx = 0
    try:
        for name, c in psutil.net_io_counters(pernic=True).items():
            low = name.lower()
            if low.startswith(("lo", "loopback")) or "pseudo" in low or "vethernet" in low:
                continue
            rx += c.bytes_recv
            tx += c.bytes_sent
    except (OSError, RuntimeError):
        pass
    return rx, tx


class _Procs:
    """占 CPU 最多的几个进程。psutil 的 cpu_percent 要两次采样，所以缓存 Process 对象。"""
    SKIP = {0}

    def __init__(self):
        self.cache = {}

    def top(self, n=4):
        seen, res = set(), []
        for p in psutil.process_iter(["name", "memory_info"]):
            pid = p.pid
            if pid in self.SKIP:
                continue
            seen.add(pid)
            proc = self.cache.get(pid)
            if proc is None:
                self.cache[pid] = p
                try:
                    p.cpu_percent(None)
                except (psutil.Error, OSError):
                    pass
                continue
            try:
                cpu = proc.cpu_percent(None)
                mi = p.info.get("memory_info")
                name = p.info.get("name") or ""
            except (psutil.Error, OSError):
                continue
            if name.lower() in ("system idle process", "idle", "system interrupts"):
                continue
            res.append({"name": name[:40], "cpu": round(cpu, 1), "mem_mb": round((mi.rss if mi else 0) / 1048576)})
        for pid in list(self.cache):
            if pid not in seen:
                del self.cache[pid]
        res.sort(key=lambda r: r["cpu"], reverse=True)
        return res[:n]


class SystemSource:
    key = "system"
    title = "电脑实时状态"
    default_interval = 2

    def __init__(self, cfg):
        psutil.cpu_percent(None)
        self.gpu = _GPU()
        self.gpu.read()
        self.procs = _Procs()
        self.procs.top()
        self.hist = {k: deque(maxlen=HISTORY_LEN) for k in ("cpu", "mem", "gpu", "rx", "tx")}
        self.prev_net = None
        self.model = None
        self.slow = {}
        self.slow_at = 0
        self.first = True

    def collect(self, force=False):
        now = time.time()
        cpu = round(psutil.cpu_percent(None), 1)
        if self.first:  # 第一次是从开机算起的平均值，不准，和 Mac 版一样先不显示
            cpu, self.first = None, False
        mem = _memory()
        gpu = self.gpu.read()
        rx, tx = _net_bytes()
        rate = None
        if self.prev_net and now > self.prev_net[0]:
            dt = now - self.prev_net[0]
            rate = (max(0, rx - self.prev_net[1]) / dt, max(0, tx - self.prev_net[2]) / dt)
        self.prev_net = (now, rx, tx)
        if force or now - self.slow_at > 30:
            self.slow = {"disk": _disk(), "battery": _battery(), "boot": int(psutil.boot_time()), "procs": self.procs.top()}
            self.slow_at = now
        elif now - self.slow_at > 6:
            self.slow["procs"] = self.procs.top()
        if self.model is None:
            self.model = {"chip": _cpu_name(), "name": _os_name(), "p": None, "e": None,
                          "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count(logical=True), "os": "win" if IS_WIN else "other"}
        for k, v in (("cpu", cpu), ("mem", mem.get("pct")), ("gpu", gpu and gpu.get("pct")),
                     ("rx", rate and rate[0]), ("tx", rate and rate[1])):
            if v is not None:
                self.hist[k].append(round(v, 1))
        disk_pct = self.slow.get("disk") and self.slow["disk"]["pct"]
        pcts = [v for v in (cpu, mem.get("pct"), disk_pct) if v is not None]
        level = "error" if any(p >= 95 for p in pcts) else "warn" if any(p >= 85 for p in pcts) else "ok"
        why = []
        for nm, v, fx in (("CPU", cpu, "点 CPU 圆环看是谁在占"), ("内存", mem.get("pct"), "点内存圆环看是谁在占"),
                          ("磁盘", disk_pct, "让 Vesper 扫一下能清的东西")):
            if v is not None and v >= 85:
                why.append({"text": "%s%s用了 %d%%" % (nm, " " if nm.isascii() else "", round(v)), "fix": fx})
        return {
            "level": level, "why": why, "sysdata": {},
            "cpu": cpu, "memory": mem, "gpu": gpu,
            "net": {"rx_bps": round(rate[0]) if rate else None, "tx_bps": round(rate[1]) if rate else None},
            "disk": self.slow.get("disk"), "battery": self.slow.get("battery"),
            "boot": self.slow.get("boot"), "procs": self.slow.get("procs", []),
            "model": self.model,
            "history": {k: list(v) for k, v in self.hist.items()},
        }
