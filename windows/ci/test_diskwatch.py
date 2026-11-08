"""检查 Windows 版 diskwatch（磁盘「谁在占空间」）：函数都在、返回的数据结构和 Mac 版一样。

用法：python windows/ci/test_diskwatch.py（Windows 上跑全部检查；Linux 上加 DECK_OS=win 也能跑，只查不依赖 Windows 的部分）。
失败时退出码不是 0。
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MANAGER = os.path.join(os.path.dirname(os.path.dirname(HERE)), "manager")
sys.path.insert(0, MANAGER)
IS_WIN = sys.platform == "win32"

FAIL = []
PASS = [0]


def check(ok, what):
    if ok:
        PASS[0] += 1
    else:
        FAIL.append(what)
        print("失败：" + what)


def has(d, keys, what):
    check(isinstance(d, dict), what + " 应该是 dict")
    if isinstance(d, dict):
        miss = [k for k in keys if k not in d]
        check(not miss, "%s 缺少 %s" % (what, miss))


import winport  # noqa: E402

done = winport.install()
if IS_WIN:
    check("diskwatch" in done, "install() 应该登记 diskwatch")
import diskwatch  # noqa: E402

check(getattr(diskwatch, "__winport__", False) or not IS_WIN, "导入的应该是 Windows 版")
if not getattr(diskwatch, "__winport__", False):  # Linux 上 install() 也会登记；保险起见直接拿 Windows 版
    from winport import diskwatch  # noqa: E402,F811

for name in ("volumes", "overview", "growth", "recent", "WRITERS", "reveal", "start", "owner", "snapshot", "source", "has_fda"):
    check(hasattr(diskwatch, name), "缺少 " + name)

# 盘列表
vols = diskwatch.volumes(max_age=0)
check(isinstance(vols, list), "volumes() 应该是 list")
for v in vols:
    has(v, ("id", "name", "mount", "total", "free", "pct", "ext"), "volumes() 的一项")
if vols:
    check(vols[0]["id"] == "mac" and vols[0]["ext"] is False, "第一块应该是系统盘，id 为 mac")
if IS_WIN:
    check(bool(vols), "Windows 上至少要有系统盘")

# 总览
ov = diskwatch.overview("mac")
has(ov, ("vols", "vid", "day_drop", "day_span", "top", "snap_at", "fda"), "overview()")
for v in ov.get("vols", []):
    has(v, ("id", "name", "total", "free", "pct", "ext"), "overview().vols 的一项")

# 谁在变大
for win in (3600, 86400, 7 * 86400):
    g = diskwatch.growth("mac", win, 10)
    has(g, ("state", "rows"), "growth()")
    check(g.get("state") in ("waiting", "first", "ok"), "growth().state 不对：%r" % g.get("state"))
    for r in g.get("rows", []):
        has(r, ("path", "full", "name", "app", "desc", "size", "delta", "new"), "growth().rows 的一项")

# 量一小块（不跑整个用户文件夹）：临时目录里放 60MB 的文件，看能不能数出来
tmp = tempfile.mkdtemp(prefix="deck-dw-")
big = os.path.join(tmp, "big")
os.makedirs(big)
with open(os.path.join(big, "a.bin"), "wb") as f:
    f.write(b"\0" * (60 * 1024 * 1024))
b = diskwatch._Budget(100000, 30, pause=False)
sizes = diskwatch._measure(tmp, 2, b)
check(sizes.get(tmp, 0) >= 50 * 1024 * 1024, "_measure() 应该数出临时目录 ≥50MB")
check(big in sizes, "_measure() 应该记下 ≥50MB 的子文件夹")

# owner：按路径认应用
if IS_WIN:
    home = os.path.expanduser("~")
    check(diskwatch.owner(os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data"))[0] == "Chrome", "Chrome 的目录应该认成 Chrome")
    check(diskwatch.owner(os.path.join(home, "AppData", "Local", "Packages", "Microsoft.WindowsTerminal_8wekyb3d8bbwe"))[0] == "WindowsTerminal",
          "应用商店应用应该认出名字")
    pf = os.environ.get("ProgramFiles")
    if pf:
        check(diskwatch.owner(os.path.join(pf, "Foo App"))[0] == "Foo App", "Program Files 下的应该认成那个应用")
check(isinstance(diskwatch.owner("/nowhere/x"), tuple), "owner() 应该返回 (应用名, 说明)")

# 最近新增：在下载文件夹（没有就用临时目录）放一个新文件，看能不能找到
dl = os.path.join(os.path.expanduser("~"), "Downloads")
probe_dir = dl if IS_WIN and os.path.isdir(dl) else tmp
probe = os.path.join(probe_dir, "deck-diskwatch-test-%d.txt" % os.getpid())
with open(probe, "w", encoding="utf-8") as f:
    f.write("deck")
try:
    r = diskwatch.recent("mac")
    has(r, ("rows",), "recent()")
    t0 = time.time()
    while r.get("loading") and time.time() - t0 < 90:
        time.sleep(1)
        r = diskwatch.recent("mac")
    check(not r.get("loading"), "recent() 90 秒内应该查完")
    for row in r.get("rows", []):
        has(row, ("path", "full", "name", "dir", "size", "added", "src", "cls", "desc"), "recent().rows 的一项")
    if probe_dir == dl:
        hit = any(os.path.normcase(row["full"]) == os.path.normcase(probe) for row in r.get("rows", []))
        check(hit or r.get("note"), "recent() 应该找到刚放进下载文件夹的文件")
    # 直接查临时目录（不受下载文件夹里东西多少影响）
    found = {}
    diskwatch._scan_new(tmp, time.time() - 3600, found, diskwatch._Budget(100000, 30, pause=False))
    check(big in found, "_scan_new() 应该找到刚建的文件夹")
finally:
    try:
        os.remove(probe)
    except OSError:
        pass

# 谁在写硬盘
diskwatch.WRITERS.sample()
w = diskwatch.WRITERS.top(600, 12)
has(w, ("rows", "span", "since", "at"), "WRITERS.top()")
for row in w.get("rows", []):
    has(row, ("name", "sys", "bytes", "total", "procs"), "WRITERS.top().rows 的一项")
w0 = diskwatch.WRITERS.top(0, 12)
has(w0, ("rows", "span", "since", "at"), "WRITERS.top(0)")
if IS_WIN:
    check(len(diskwatch.WRITERS.samples[-1][1]) > 0, "Windows 上应该读得到至少一个进程的写入量")

# 在资源管理器中显示：没出现在列表里的路径要拒绝
rv = diskwatch.reveal(os.path.join(tmp, "big", "a.bin"))
check(isinstance(rv, dict) and rv.get("error"), "reveal() 应该拒绝没在列表里出现过的路径")
rv = diskwatch.reveal("")
check(isinstance(rv, dict) and rv.get("error"), "reveal() 应该拒绝空路径")

try:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
except OSError:
    pass

print("diskwatch：通过 %d 项，失败 %d 项（%s）" % (PASS[0], len(FAIL), "Windows" if IS_WIN else "非 Windows，只查通用部分"))
sys.exit(1 if FAIL else 0)
