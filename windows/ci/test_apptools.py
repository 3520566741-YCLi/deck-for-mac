"""工具箱和键盘清洁（Windows 版）的自检。普通脚本，有问题就以非 0 退出。

在 Windows 上：列已装应用、跑垃圾扫描和找大文件、看键盘清洁的状态，锁 2 秒键盘看它会不会自己解锁。
在 Linux 上（DECK_OS=win 开发）：只检查能导入、返回的结构对，Windows 才有的检查跳过。
整个脚本最多跑 240 秒，超时直接退出，CI 不会卡住。
用法：python windows/ci/test_apptools.py
"""
import faulthandler
import os
import sys
import threading
import time

HARD_LIMIT = 240
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "manager"))
IS_WIN = sys.platform == "win32"
FAILS = []


def _hard_stop():
    print("超时 %d 秒，强制退出" % HARD_LIMIT, flush=True)
    faulthandler.dump_traceback(all_threads=True)
    os._exit(3)


_t = threading.Timer(HARD_LIMIT, _hard_stop)
_t.daemon = True
_t.start()


def check(cond, what):
    print(("通过  " if cond else "失败  ") + what, flush=True)
    if not cond:
        FAILS.append(what)


def skip(what):
    print("跳过  " + what + "（只在 Windows 上测）", flush=True)


from winport import apptools, kbclean  # noqa: E402

# ---- 应用列表 ----
apps = apptools.list_apps()
check(isinstance(apps, list), "list_apps 返回列表")
if IS_WIN:
    check(len(apps) > 0, "列出了已装的应用（%d 个）" % len(apps))
    keys = {"path", "name", "bid", "version", "bytes", "last_used", "mas", "brew", "sparkle", "running", "protected"}
    check(all(keys <= set(a) and a["name"] for a in apps), "每个应用都有名字和 Mac 版那些字段")
    check(all(a["path"].startswith("reg:") for a in apps), "应用的 path 是注册表登记")
    sample = next((a for a in apps if not a["protected"]), None)
    if sample:
        t0 = time.time()
        lo = apptools.find_leftovers(sample["path"], apps)
        check(isinstance(lo.get("items"), list), "找残留（%s，%.1f 秒，%d 项，不动文件）" % (sample["name"], time.time() - t0, len(lo["items"])))
else:
    check(apps == [], "非 Windows 上应用列表是空的")
    skip("已装应用数量")
    check("error" in apptools.uninstall_plan("reg:HKLM:x"), "非 Windows 上卸载返回错误")

# ---- 路径保护 ----
home = os.path.expanduser("~")
check(apptools._safe_target(home) is not None, "家目录本身不能移走")
check(apptools._safe_target("relative/path") is not None, "相对路径不能移走")
if IS_WIN:
    check(apptools._safe_target(os.environ.get("WINDIR", r"C:\Windows")) is not None, "Windows 文件夹不能移走")
    check(apptools._safe_target(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")) is not None, "System32 不能移走")
    check(apptools._safe_target(os.environ.get("APPDATA", "")) is not None, "%APPDATA% 本身不能移走")

# ---- winget 表格解析 ----
sample_out = ("\r-\r\\\rName           Id                 Version  Available Source\n"
              "--------------------------------------------------------------\n"
              "Git            Git.Git            2.40.0   2.41.0    winget\n"
              "微信           Tencent.WeChat     < 3.9    3.9.12    winget\n"
              "Some Store App 9NBLGGH4NNS1       1.0      1.1       msstore\n"
              "3 upgrades available.\n")
rows = apptools._winget_rows(sample_out)
check([r["token"] for r in rows] == ["Git.Git", "Tencent.WeChat", "9NBLGGH4NNS1"], "解析 winget upgrade 表格")
check(rows and rows[1]["from"] == "< 3.9" and rows[1]["name"] == "微信", "winget 表格里的中文名字和「< 版本」")

# ---- 垃圾扫描 ----
t0 = time.time()
junk = apptools.junk_scan()
check(isinstance(junk, dict) and isinstance(junk.get("rules"), list) and "id" in junk and "bytes" in junk,
      "垃圾扫描的结构（%.1f 秒，%d 类）" % (time.time() - t0, len(junk.get("rules") or [])))
for r in junk.get("rules") or []:
    if not ({"id", "group", "label", "on", "count", "bytes", "examples", "items"} <= set(r)):
        check(False, "垃圾规则 %s 的字段" % r.get("id"))
check(apptools.cancel(junk["id"]).get("ok"), "取消垃圾清单")
check("error" in apptools.junk_execute(junk["id"], ["user-temp"]), "取消后的清单不能再执行")

# ---- 找大文件（只列出，不动） ----
t0 = time.time()
fs = apptools.files_scan(500)
check(isinstance(fs.get("items"), list) and isinstance(fs.get("dups"), list) and fs.get("id"),
      "找大文件的结构（%.1f 秒，%d 个）" % (time.time() - t0, len(fs.get("items") or [])))
apptools.cancel(fs["id"])

# ---- 摘要、更新、后台任务 ----
s = apptools.summary()
check({"updates", "updates_at", "update_names", "junk_last", "brew"} <= set(s), "工具箱卡片摘要的字段")
check(isinstance(apptools.human(123456789), str), "human()")
jid = apptools.start_job("space", apptools.space_overview)
t0 = time.time()
while apptools.job(jid).get("state") == "running" and time.time() - t0 < 90:
    time.sleep(0.5)
j = apptools.job(jid)
check(j.get("state") in ("done", "error") and isinstance(j.get("result"), (list, dict)), "后台任务（空间分布）跑完了")

# ---- 键盘清洁 ----
st = kbclean.status()
check({"installed", "durations", "now", "state"} <= set(st) and st["state"] in ("idle", "locked"), "键盘清洁 status 的结构")
check(kbclean.open_settings().get("ok"), "open_settings")
if IS_WIN:
    kbclean.MIN_SECONDS = 1
    t0 = time.time()
    r = kbclean.start(2, black=True, mouse=False)
    if r.get("error"):
        print("注意  这台机器装不上键盘钩子：%s（CI 可能没有交互桌面），自动解锁这项跳过" % r["error"], flush=True)
    else:
        check(r.get("state") == "locked", "开始清洁后是锁着的")
        check(kbclean.extend().get("until", 0) <= r.get("max", 0), "加时不超过上限")
        kbclean._cur["s"].until = time.time() + 2  # 加时把截止往后推了 30 秒：拨回 2 秒后，再看到点会不会自己解锁
        t0 = time.time()
        while kbclean.status().get("state") == "locked" and time.time() - t0 < 20:
            time.sleep(0.2)
        st = kbclean.status()
        check(st.get("state") == "idle", "到点自动解锁（%.1f 秒，原因 %s）" % (time.time() - t0, st.get("last_reason")))
        r = kbclean.start(30)
        if r.get("state") == "locked":
            r = kbclean.stop()
            check(r.get("state") == "idle" and r.get("unlocked"), "点「解锁」马上解锁")
else:
    check("error" in kbclean.start(2), "非 Windows 上开始清洁返回错误")
    skip("锁 2 秒后自动解锁")

print("\n%s：%d 项失败" % ("没通过" if FAILS else "全部通过", len(FAILS)), flush=True)
for f in FAILS:
    print("  - " + f)
sys.exit(1 if FAILS else 0)
