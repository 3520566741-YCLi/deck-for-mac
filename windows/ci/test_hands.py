"""Vesper 的手（Windows 版，winport/hands_win.py + winport/appctl.py + hands.py 的 Windows 分支）的自检。

任何系统上（Linux 用 DECK_OS=win 模拟）：能导入、call() 的返回结构和 DeckHands.swift 一样、开关默认关并能存、
急停 / 恢复、快捷键解析、hands.py 在 Windows 下的危险名单和按键换算、.lnk 解析。
在真 Windows 上（GitHub windows-latest 有可交互的桌面）：开一个记事本，读元素、找到编辑区、打字「Deck 测试 123」再读回来、
Ctrl+A、退格、屏幕变了不点、急停快捷键、按名字读元素和切换、点一个菜单（编辑 / 查看），最后不保存关掉记事本。
记事本兼顾老版（Win10，Edit 控件）和 Win11 的新版（商店应用、带标签页、RichEdit 文档）。
整个脚本最多跑 180 秒，超时直接退出，CI 不会卡住。不改真实的 Deck 设置：APPDATA / LOCALAPPDATA / HOME 都指到临时目录。
用法：python windows/ci/test_hands.py
"""
import faulthandler
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time

HARD_LIMIT = 180
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IS_WIN = sys.platform == "win32"
FAILS = []
TMP = tempfile.mkdtemp(prefix="deck-hands-")
# 在导入任何 Deck 模块之前，把配置目录指到临时目录（开关文件 hands.json、日志都写这里）
for k in ("APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
    os.environ[k] = TMP
os.environ.setdefault("DECK_OS", "win")
sys.path.insert(0, os.path.join(ROOT, "manager"))


def _hard_stop():
    print("超时 %d 秒，强制退出" % HARD_LIMIT, flush=True)
    faulthandler.dump_traceback(all_threads=True)
    _cleanup()
    os._exit(3)


_t = threading.Timer(HARD_LIMIT, _hard_stop)
_t.daemon = True
_t.start()


def check(cond, what):
    print(("通过  " if cond else "失败  ") + what, flush=True)
    if not cond:
        FAILS.append(what)
    return cond


def skip(what):
    print("跳过  " + what + "（只在 Windows 上测）", flush=True)


NEW_WINDOWS = []  # 测试开出来的记事本窗口，最后一定关掉


def _cleanup():
    if not IS_WIN:
        return
    try:
        from winport import appctl  # noqa: PLC0415
        pids = {appctl.real_pid(h) for h in NEW_WINDOWS}
        for p in pids:
            if p:
                subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True, timeout=10)  # noqa: S603,S607
    except Exception as e:  # noqa: BLE001
        print("清理出错：%s" % e, flush=True)


import deckos  # noqa: E402
import hands  # noqa: E402
from winport import appctl, hands_win  # noqa: E402

check(deckos.WIN, "DECK_OS=win 或真 Windows：deckos.WIN 为真")

# ---- 协议的形状（任何系统）----
st = hands_win.call({"cmd": "status"})
check({"ok", "trusted", "enabled", "stopped", "front"} <= set(st), "status 的字段和 DeckHands 一样")
check({"name", "bundle", "pid"} <= set(st["front"]), "status.front 有 name / bundle / pid")
check(st["enabled"] is False, "「允许 Vesper 操作电脑」默认关着")
check(st["trusted"] is IS_WIN, "trusted：Windows 上为真（不需要授权），别的系统为假")
check(hands_win.call({"cmd": "nope"}) == {"ok": False, "error": "未知命令"}, "未知命令")
check(hands_win.call("x")["ok"] is False, "请求不是 dict 时不抛异常")
check(hands_win.call({"cmd": "stop"})["stopped"] is True, "stop 后 stopped 为真")
check(hands_win.call({"cmd": "arm"})["stopped"] is False, "arm 后 stopped 恢复")
st = hands_win.set_enabled(True)
check(st["enabled"] is True and os.path.isfile(deckos.support("Deck", "hands.json")), "打开开关并存进 hands.json")
check(os.path.realpath(deckos.support("Deck", "hands.json")).startswith(os.path.realpath(TMP)), "开关文件在临时目录里")
hands_win.set_enabled(False)
r = hands_win.call({"cmd": "press", "snap": "x", "i": 0})
check(r["ok"] is False and r["error"], "开关关着时动作被拒绝（%s）" % r["error"])
hands_win.set_enabled(True)
if not IS_WIN:
    for cmd in ("elements", "press", "type", "key", "scroll", "open"):
        r = hands_win.call({"cmd": cmd})
        check(r == {"ok": False, "error": hands_win.NOT_WIN}, "非 Windows 上 %s 返回「只能在 Windows 上用」" % cmd)

# ---- 快捷键解析 ----
check(hands_win.parse_keys("ctrl+shift+t")[0] == ([0x11, 0x10], 0x54), "ctrl+shift+t")
check(hands_win.parse_keys("cmd+c")[0] == ([0x11], 0x43), "cmd 按 ctrl 算")
check(hands_win.parse_keys("win+d")[0] == ([0x5B], 0x44), "win 是徽标键")
check(hands_win.parse_keys("opt+f4")[0] == ([0x12], 0x73), "opt 是 Alt")
check(hands_win.parse_keys("ctrl++")[0] == ([0x11], 0xBB), "ctrl++ 的主键是 +")
check(hands_win.parse_keys("ctrl+alt+delete")[0] is None, "Ctrl+Alt+Del 拒绝（系统保留）")
check(hands_win.parse_keys("ctrl")[0] is None, "只有修饰键时拒绝")
check(hands_win.parse_keys("ctrl+foo")[1] == "不认识的按键：foo", "不认识的键")

# ---- hands.py 的 Windows 分支 ----
check(hands._norm_keys("Cmd+Q") == "ctrl+q", "hands._norm_keys：cmd → ctrl")
check(hands._norm_keys("alt+F4") == "opt+f4" and "opt+f4" in hands.DANGER_KEYS, "Alt+F4 是危险快捷键")
check(hands._norm_keys("Win+L") in hands.DANGER_KEYS, "Win+L 是危险快捷键")
check(hands._norm_keys("shift+del") == "shift+delete" in hands.DANGER_KEYS, "Shift+Del 是危险快捷键")
check(hands._norm_keys("backspace") == "backspace", "Windows 上退格不当作 delete")
H = hands.Hands(lambda: None)
check(H._risk("type", "cmd.exe", text="dir") == "要在终端里输入命令", "在 cmd.exe 里打字要确认")
check(H._risk("key", "wechat.exe", keys="return") == "可能会把消息发出去", "在微信里按回车要确认")
check(bool(H._risk("click", "1password.exe", label="打开")), "密码管理器里点任何东西都要确认")
check(bool(H._risk("click", "explorer.exe:credentials", label="显示")), "凭据管理器里点任何东西都要确认")
check(bool(H._risk("key", "explorer.exe", keys="delete")), "资源管理器里按 Del 要确认")
check(H._risk("key", "notepad.exe", keys="ctrl+a") is None, "记事本里 Ctrl+A 直接做")
check(bool(H._risk("click", "notepad.exe", label="不保存")), "「不保存」要确认")
check(H._call({"cmd": "status"}).get("ok") is True, "hands.py 在 Windows 上不走 Unix socket，直接调 hands_win")
H._call({"cmd": "arm"})
if not IS_WIN:
    try:
        H._ready()
        check(False, "非 Windows 上 _ready 应该报错")
    except hands.HandsError as e:
        check("这台电脑上用不了" in str(e), "非 Windows 上 _ready 说清楚原因（%s）" % e)

# ---- .lnk 解析（造一个最小的快捷方式文件）----
target = b"C:\\Program Files\\Deck\\Deck.exe\0"
li_hdr = 0x1C
li = struct.pack("<IIIIIII", li_hdr + len(target) + 1, li_hdr, 1, li_hdr, li_hdr, 0, li_hdr + len(target)) + target + b"\0"
hdr = bytearray(0x4C)
hdr[0:4] = b"\x4c\0\0\0"
struct.pack_into("<I", hdr, 0x14, 0x2)  # HasLinkInfo
lnk = os.path.join(TMP, "Deck.lnk")
with open(lnk, "wb") as f:
    f.write(bytes(hdr) + li)
check(appctl.lnk_target(lnk) == "C:\\Program Files\\Deck\\Deck.exe", "读出 .lnk 指向的 exe")
check(appctl._bad_name("C:\\x\\evil.exe") and appctl._bad_name("-x") and not appctl._bad_name("微信"), "应用名里不许带路径")
if not IS_WIN:
    check(appctl.find("notepad") == (None, []), "非 Windows 上找不到应用")


# ---- 真 Windows：记事本 ----
def wait(fn, secs, step=0.3):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


# 测哪个应用：先用系统记事本；云电脑（Windows Server 2025）上 notepad.exe 会直接崩（0xC0000005，没装商店版记事本），
# 就换成 testpad.ps1（同样是标准窗口：菜单栏、文本框、关窗口问「不保存」）。
APP = {"exe": "notepad.exe", "title": "", "name": "notepad"}


def notepad_windows():
    return {h for h, p in appctl.app_windows() if os.path.basename(appctl.exe_of(p)).lower() == APP["exe"]
            and APP["title"] in appctl.title_of(h)}


def first(els, pred):
    return next((e for e in els if pred(e)), None)


def run_notepad():
    before = notepad_windows()
    proc = subprocess.Popen(["notepad.exe"])  # noqa: S603,S607
    new = wait(lambda: notepad_windows() - before or proc.poll() not in (None, 0), 25)
    if not new or new is True:
        print("      notepad.exe 没开出窗口（退出码 %s），换成 testpad.ps1" % proc.poll(), flush=True)
        APP.update(exe="powershell.exe", title="Deck 测试记事本", name="Deck 测试记事本")
        before = notepad_windows()
        ps1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testpad.ps1")
        proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1])  # noqa: S603,S607
        new = wait(lambda: notepad_windows() - before, 40)
    if not check(bool(new) and new is not True, "记事本开起来了（%s）" % APP["exe"]):
        # 看看到底开出了什么：进程还在不在、屏幕上有哪些窗口
        print("      notepad 进程：pid=%s 退出码=%s" % (proc.pid, proc.poll()), flush=True)
        for h, p in appctl.app_windows()[:25]:
            print("      窗口：%s | %s | %s" % (os.path.basename(appctl.exe_of(p)) or p, appctl.title_of(h)[:40], appctl.class_of(h)), flush=True)
        try:
            import psutil  # noqa: PLC0415
            print("      notepad 相关进程：%s" % [(x.pid, x.info["name"]) for x in psutil.process_iter(["name"]) if "notepad" in (x.info["name"] or "").lower()], flush=True)
        except Exception as e:  # noqa: BLE001
            print("      psutil：", e)
        return
    NEW_WINDOWS.extend(new)
    hwnd = sorted(new)[0]
    pid = appctl.real_pid(hwnd)
    time.sleep(1.5)  # 新版记事本启动后还要加载一会儿
    appctl.activate(hwnd)
    print("      记事本：pid=%d 标题=%s 类=%s" % (pid, appctl.title_of(hwnd), appctl.class_of(hwnd)), flush=True)

    t0 = time.time()
    scr = hands_win.call({"cmd": "elements", "pid": pid, "max": 250})
    if not check(scr.get("ok"), "读出记事本的元素（%.1f 秒，%d 个）%s" % (time.time() - t0, len(scr.get("elements") or []),
                                                                 scr.get("error") or "")):
        return
    check(scr["bundle"] == APP["exe"], "应用身份是 exe 名（%s）" % scr["bundle"])
    els = scr["elements"]
    keys = {"i", "role", "label", "enabled"}
    check(all(keys <= set(e) for e in els), "每个元素都有 i / role / label / enabled")
    check(all(e["i"] == k for k, e in enumerate(els)), "i 从 0 连续编号")
    for e in els[:40]:
        print("      %s" % e, flush=True)
    edit = first(els, lambda e: e.get("text") and e["role"] in ("Edit", "Document"))
    if not check(edit is not None, "找到编辑区（Edit 或 Document，text=True）"):
        return
    check("secure" not in edit, "编辑区不是密码框")

    r = hands_win.call({"cmd": "type", "text": "Deck 测试 123", "snap": scr["snap"], "i": edit["i"]}, 30)
    check(r.get("ok") and r.get("chars") == len("Deck 测试 123"), "打字「Deck 测试 123」%s" % (r.get("error") or ""))
    time.sleep(0.5)
    got = hands_win.text_of(scr["snap"], edit["i"])
    check(isinstance(got, str) and "Deck 测试 123" in got, "读回来的文字对（%r）" % (got[:60] if isinstance(got, str) else got))

    r = hands_win.call({"cmd": "key", "keys": "ctrl+a"})
    check(r.get("ok"), "按 Ctrl+A")
    r = hands_win.call({"cmd": "key", "keys": "backspace"})
    check(r.get("ok"), "按退格")
    time.sleep(0.4)
    got = hands_win.text_of(scr["snap"], edit["i"])
    check(isinstance(got, str) and "Deck" not in got, "全选后退格，文字没了（%r）" % (got[:60] if isinstance(got, str) else got))
    hands_win.call({"cmd": "type", "text": "x"})  # 让文档「有改动」，关的时候会问要不要保存

    r = hands_win.call({"cmd": "scroll", "dir": "down", "amount": 3})
    check(r.get("ok"), "滚动")

    # 急停：程序自己按 Ctrl+Alt+Esc 也会触发全局快捷键
    st = hands_win.call({"cmd": "status"})
    if st.get("hotkeys"):
        hands_win.call({"cmd": "key", "keys": "ctrl+opt+esc"})
        check(wait(lambda: hands_win.call({"cmd": "status"})["stopped"], 3), "急停快捷键 %s 生效" % st["hotkeys"][0])
        r = hands_win.call({"cmd": "type", "text": "不该打出来"})
        check(r.get("ok") is False and "急停" in r.get("error", ""), "急停后拒绝打字")
        hands_win.call({"cmd": "arm"})
    else:
        check(False, "注册急停快捷键（Ctrl+Alt+Esc / Ctrl+Alt+. 都被占了？）")

    # 屏幕变了不点：把窗口挪开 120 像素，再用旧的那批元素去点
    rect = appctl.rect_of(hwnd)
    import ctypes  # noqa: PLC0415
    u32 = ctypes.windll.user32
    u32.ShowWindow(ctypes.c_void_p(hwnd), 9)
    u32.SetWindowPos(ctypes.c_void_p(hwnd), None, rect[0] + 120, rect[1] + 120, 0, 0, 0x0001 | 0x0004 | 0x0010)
    time.sleep(0.6)
    r = hands_win.call({"cmd": "press", "snap": scr["snap"], "i": edit["i"]})
    check(r.get("ok") is False and "屏幕变了" in r.get("error", ""), "窗口挪了以后不按旧位置点（%s）" % r.get("error"))
    r = hands_win.call({"cmd": "press", "snap": "nope", "i": 0})
    check(r.get("ok") is False and "过期" in r.get("error", ""), "不认识的 snap 被拒绝")

    # 按名字读、按名字切换（不会再开一个）
    scr2 = hands_win.call({"cmd": "elements", "name": APP["name"]})
    check(scr2.get("ok") and scr2.get("bundle") == APP["exe"], "按名字 %s 读元素 %s" % (APP["name"], scr2.get("error") or ""))
    before = notepad_windows()
    r = hands_win.call({"cmd": "open", "name": APP["name"]}, 30)
    check(r.get("ok") and r.get("how") == "activated", "open %s：已经开着就切过去（%s）" % (APP["name"], r))
    time.sleep(1.0)
    extra = notepad_windows() - before
    NEW_WINDOWS.extend(extra)
    check(not extra, "没有多开一个记事本")

    # 点一个菜单（编辑 / 查看：只是展开菜单，不改任何东西），再按 Esc 收起
    appctl.activate(hwnd)
    scr3 = hands_win.call({"cmd": "elements", "pid": pid})
    menu = first(scr3.get("elements") or [], lambda e: e["role"] == "MenuItem" and e["label"].replace("(&E)", "").replace("(E)", "")
                 .strip() in ("编辑", "Edit", "查看", "View"))
    if check(menu is not None, "找到菜单「编辑」或「查看」（%s）" % [e["label"] for e in scr3.get("elements") or [] if e.get("menu")]):
        r = hands_win.call({"cmd": "press", "snap": scr3["snap"], "i": menu["i"]})
        check(r.get("ok"), "点菜单「%s」（%s）%s" % (menu["label"], r.get("method"), r.get("error") or ""))
        time.sleep(0.6)
        hands_win.call({"cmd": "key", "keys": "esc"})
        hands_win.call({"cmd": "key", "keys": "esc"})

    # 不保存关掉：发「关闭」，出现保存提示就点「不保存」；还在就结束进程（是这个测试自己开的）
    for h in list(NEW_WINDOWS):
        u32.PostMessageW(ctypes.c_void_p(h), 0x0010, 0, 0)
    time.sleep(1.2)
    closed = False
    for _ in range(3):
        if not (notepad_windows() & set(NEW_WINDOWS)) and not [h for h in appctl.top_windows() if appctl.real_pid(h) == pid]:
            closed = True
            break
        scr4 = hands_win.call({"cmd": "elements", "pid": pid})
        btn = first(scr4.get("elements") or [], lambda e: e["role"] == "Button" and any(
            w in e["label"] for w in ("不保存", "Don't save", "Don’t save", "Don't Save", "Don’t Save")))
        if btn:
            r = hands_win.call({"cmd": "press", "snap": scr4["snap"], "i": btn["i"]})
            print("      点「%s」：%s" % (btn["label"], r), flush=True)
        time.sleep(1.2)
    check(closed or wait(lambda: not (notepad_windows() & set(NEW_WINDOWS)), 4), "记事本不保存关掉了")


if IS_WIN:
    try:
        run_notepad()
    except Exception as e:  # noqa: BLE001
        import traceback  # noqa: PLC0415
        traceback.print_exc()
        check(False, "记事本测试出错：%s" % e)
    finally:
        _cleanup()
else:
    skip("记事本：读元素 / 打字 / 按键 / 点菜单 / 关闭")

print("\n%d 项失败" % len(FAILS) if FAILS else "\n全部通过", flush=True)
os._exit(1 if FAILS else 0)
